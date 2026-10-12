import json
import math
import os
import threading
import time

from unitree_sdk2py.core.channel import (
    ChannelFactoryInitialize,
    ChannelPublisher,
    ChannelSubscriber,
)
from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient, action_map
from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient
from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_, LowState_
from unitree_sdk2py.utils.crc import CRC

# Tiempo estimado para que el gesto termine su trayectoria y el robot
# reabsorba el desplazamiento de su centro de masa antes de aceptar otro gesto.
GESTURE_SETTLE_SECONDS = 3.0

# Inclinacion maxima (roll/pitch, en radianes) que se considera "de pie y estable".
# ~10 grados. Si el robot esta mas inclinado que esto (cayendo, en transicion,
# acostado, etc.) no se dispara el gesto.
MAX_STABLE_TILT_RAD = math.radians(10)

# Cuanto esperar por el primer mensaje de rt/lowstate al arrancar el suscriptor.
FIRST_STATE_TIMEOUT_SECONDS = 2.0

# Reintentos de lectura si el robot esta inclinado: le da tiempo a su propio
# controlador de balance para corregir una inclinacion transitoria antes de
# rendirnos y omitir el gesto. No mandamos ningun comando de postura nosotros,
# solo esperamos y volvemos a leer.
STABILITY_RETRY_ATTEMPTS = 4
STABILITY_RETRY_DELAY_SECONDS = 0.3

# Antes de cualquier gesto que mueva el brazo cerca del cuerpo, primero
# estiramos bien las piernas (altura maxima de pie) para darle mas espacio
# al brazo y reducir el riesgo de que roce con la pierna.
STAND_TALL_SETTLE_SECONDS = 1.0

# Pausa entre las dos etapas del saludo de mano (extender -> menear/retirar),
# tal como lo usa el ejemplo oficial del SDK para LocoClient.ShakeHand().
SHAKE_HAND_STAGE_DELAY_SECONDS = 2.0

# --- Reproduccion de poses guardadas (brazos + cintura), capturadas con
# motor_teleop.py --------------------------------------------------------
# Mismos indices de articulacion que motor_teleop.py (G1JointIndex oficial).
POSE_JOINTS = [
    "cintura_yaw", "cintura_roll", "cintura_pitch",
    "hombro_izq_pitch", "hombro_izq_roll", "hombro_izq_yaw", "codo_izq",
    "muneca_izq_roll", "muneca_izq_pitch", "muneca_izq_yaw",
    "hombro_der_pitch", "hombro_der_roll", "hombro_der_yaw", "codo_der",
    "muneca_der_roll", "muneca_der_pitch", "muneca_der_yaw",
]
POSE_JOINT_INDEX = {
    "cintura_yaw": 12, "cintura_roll": 13, "cintura_pitch": 14,
    "hombro_izq_pitch": 15, "hombro_izq_roll": 16, "hombro_izq_yaw": 17, "codo_izq": 18,
    "muneca_izq_roll": 19, "muneca_izq_pitch": 20, "muneca_izq_yaw": 21,
    "hombro_der_pitch": 22, "hombro_der_roll": 23, "hombro_der_yaw": 24, "codo_der": 25,
    "muneca_der_roll": 26, "muneca_der_pitch": 27, "muneca_der_yaw": 28,
}
WAIST_JOINTS = {"cintura_yaw", "cintura_roll", "cintura_pitch"}
ARM_ONLY_JOINTS = [name for name in POSE_JOINTS if name not in WAIST_JOINTS]
POSE_WEIGHT_JOINT = 29

POSE_CONTROL_DT_SECONDS = 0.02
POSE_RAMP_SECONDS = 0.6
POSE_TRANSITION_SECONDS = 2.0
POSE_HOLD_SECONDS = 1.5
# kd mas alto que el 1.5 del ejemplo oficial de Unitree: con 1.5 el primer
# meneo de mano salio muy brusco (ver robot_wrist_wiggle.py). Con 3.0 el
# movimiento sale suave, sin "totazos".
POSE_KP = 60.0
POSE_KD = 3.0

_pose_publisher = None

_channel_ready = False
_arm_client = None
_loco_client = None
_gesture_lock = threading.Lock()

_low_state_subscriber = None
_latest_low_state = None
_low_state_lock = threading.Lock()


def _on_low_state(msg: LowState_) -> None:
    global _latest_low_state

    with _low_state_lock:
        _latest_low_state = msg


def _ensure_channel(network_interface: str) -> None:
    global _channel_ready

    if not _channel_ready:
        ChannelFactoryInitialize(0, network_interface)
        _channel_ready = True


def _get_arm_client(network_interface: str) -> G1ArmActionClient:
    global _arm_client

    _ensure_channel(network_interface)

    if _arm_client is None:
        _arm_client = G1ArmActionClient()
        _arm_client.SetTimeout(10.0)
        _arm_client.Init()

    return _arm_client


def _get_loco_client(network_interface: str) -> LocoClient:
    global _loco_client

    _ensure_channel(network_interface)

    if _loco_client is None:
        _loco_client = LocoClient()
        _loco_client.SetTimeout(10.0)
        _loco_client.Init()

    return _loco_client


def _stand_tall(network_interface: str) -> None:
    """Estira las piernas a altura maxima antes de un gesto de brazo, para
    darle mas despeje y reducir el riesgo de que el brazo roce la pierna."""
    loco_client = _get_loco_client(network_interface)
    loco_client.HighStand()
    time.sleep(STAND_TALL_SETTLE_SECONDS)


def _ensure_low_state_subscriber(network_interface: str) -> None:
    global _low_state_subscriber

    _ensure_channel(network_interface)

    if _low_state_subscriber is None:
        _low_state_subscriber = ChannelSubscriber("rt/lowstate", LowState_)
        _low_state_subscriber.Init(_on_low_state, 10)


def _read_tilt():
    """Devuelve (roll, pitch) en radianes, o None si aun no hay datos."""
    waited = 0.0
    while waited < FIRST_STATE_TIMEOUT_SECONDS:
        with _low_state_lock:
            state = _latest_low_state

        if state is not None:
            break

        time.sleep(0.1)
        waited += 0.1

    if state is None:
        return None

    try:
        roll, pitch, _yaw = state.imu_state.rpy
    except (AttributeError, ValueError):
        return None

    return roll, pitch


def _is_robot_stable(network_interface: str) -> bool:
    _ensure_low_state_subscriber(network_interface)

    for attempt in range(STABILITY_RETRY_ATTEMPTS):
        tilt = _read_tilt()

        if tilt is None:
            print("[robot_gestures] No pude leer rt/lowstate, se omite el gesto por seguridad.")
            return False

        roll, pitch = tilt

        if abs(roll) <= MAX_STABLE_TILT_RAD and abs(pitch) <= MAX_STABLE_TILT_RAD:
            return True

        # Inclinacion transitoria: le damos tiempo al propio balance del robot
        # para corregirla solo, sin que nosotros mandemos ningun comando.
        if attempt < STABILITY_RETRY_ATTEMPTS - 1:
            time.sleep(STABILITY_RETRY_DELAY_SECONDS)

    print(
        f"[robot_gestures] Robot sigue inclinado tras {STABILITY_RETRY_ATTEMPTS} intentos "
        f"(roll={math.degrees(roll):.1f}, pitch={math.degrees(pitch):.1f}), se omite el gesto por seguridad."
    )
    return False


_WAVE_POSES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "poses_guardadas")
# Saludo real "hola": brazo derecho levantado por encima de la cabeza
# (capturado con motor_teleop.py usando la tecla de recentrar el limite de
# seguridad), alternando el codo entre hola_1 y hola_2 para el meneo.
# Se repite dos veces y solo mueve brazo/codo/muneca -- la cintura queda fija.
WAVE_POSE_SEQUENCE = [
    os.path.join(_WAVE_POSES_DIR, "hola_1.json"),
    os.path.join(_WAVE_POSES_DIR, "hola_2.json"),
    os.path.join(_WAVE_POSES_DIR, "hola_1.json"),
    os.path.join(_WAVE_POSES_DIR, "hola_2.json"),
]


def wave_arm(network_interface: str = "eth0") -> None:
    # Antes usaba G1ArmActionClient.ExecuteAction("high wave"), pero ese
    # servicio quedo en estado ARMSDK_OCCUPIED tras unas pruebas y no se
    # pudo liberar por software (ver notas en shake_hand). Se reemplazo por
    # una secuencia de poses propias, via play_pose_sequence (arm_sdk
    # directo, no depende del servicio bloqueado).
    play_pose_sequence(
        WAVE_POSE_SEQUENCE,
        network_interface=network_interface,
        transition_seconds=0.8,
        hold_seconds=0.3,
        joints_to_move=ARM_ONLY_JOINTS,
    )


# Secuencia real de "dar la mano y menear": 3 poses capturadas a mano con
# motor_teleop.py, identicas salvo en muneca_der_pitch (-22° / +4° / -40°),
# reproducidas en orden y terminando de vuelta en la pose 1. Reemplaza el
# intento anterior con G1ArmActionClient.ExecuteAction("shake hand"), que
# dejo de funcionar (el servicio "arm" quedo en estado ARMSDK_OCCUPIED tras
# unas pruebas y no se pudo liberar por software). play_pose_sequence usa
# arm_sdk directo, que no se ve afectado por ese bloqueo.
_POSES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "poses_guardadas")
SHAKE_HAND_POSE_SEQUENCE = [
    os.path.join(_POSES_DIR, "dar_mano_1.json"),
    os.path.join(_POSES_DIR, "dar_mano_2.json"),
    os.path.join(_POSES_DIR, "dar_mano_3.json"),
    os.path.join(_POSES_DIR, "dar_mano_1.json"),
]


def shake_hand(network_interface: str = "eth0") -> None:
    play_pose_sequence(
        SHAKE_HAND_POSE_SEQUENCE,
        network_interface=network_interface,
        transition_seconds=1.0,
        hold_seconds=0.4,
        joints_to_move=ARM_ONLY_JOINTS,
    )


def _get_pose_publisher(network_interface: str) -> ChannelPublisher:
    global _pose_publisher

    _ensure_channel(network_interface)

    if _pose_publisher is None:
        _pose_publisher = ChannelPublisher("rt/arm_sdk", LowCmd_)
        _pose_publisher.Init()

    return _pose_publisher


def warm_up(network_interface: str = "eth0") -> None:
    """Establece de antemano el canal DDS, el suscriptor de rt/lowstate y el
    publicador de rt/arm_sdk usados por los gestos, para que el primer gesto
    real no pague ese costo de conexion (y arranque mas al tiempo con la voz,
    que tiene su propio warm_up en robot_speaker.py)."""
    _ensure_low_state_subscriber(network_interface)
    _get_pose_publisher(network_interface)


def _load_pose(pose_path: str) -> dict:
    with open(pose_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    pose = {}
    for name in POSE_JOINTS:
        if name not in raw:
            raise ValueError(f"La pose '{pose_path}' no tiene el joint '{name}'.")
        pose[name] = raw[name]["rad"]

    return pose


def _publish_pose_frame(low_cmd, crc: CRC, publisher, weight: float, targets: dict) -> None:
    low_cmd.motor_cmd[POSE_WEIGHT_JOINT].q = weight

    for name in POSE_JOINTS:
        idx = POSE_JOINT_INDEX[name]
        low_cmd.motor_cmd[idx].tau = 0.0
        low_cmd.motor_cmd[idx].q = targets[name]
        low_cmd.motor_cmd[idx].dq = 0.0
        low_cmd.motor_cmd[idx].kp = POSE_KP
        low_cmd.motor_cmd[idx].kd = POSE_KD

    low_cmd.crc = crc.Crc(low_cmd)
    publisher.Write(low_cmd)


def _current_pose_estimate(target: dict) -> dict:
    """Estimacion de donde quedo el brazo si se aborto a medio camino: usa
    la lectura real mas reciente de rt/lowstate si esta disponible, y si no,
    asume que sigue en la pose objetivo."""
    with _low_state_lock:
        state = _latest_low_state

    if state is None:
        return dict(target)

    return {name: state.motor_state[POSE_JOINT_INDEX[name]].q for name in POSE_JOINTS}


def play_pose_sequence(
    pose_paths: list,
    network_interface: str = "eth0",
    transition_seconds: float = POSE_TRANSITION_SECONDS,
    hold_seconds: float = POSE_HOLD_SECONDS,
    joints_to_move: list = None,
) -> None:
    """
    Lleva brazos+cintura desde su posicion actual (se asume "brazos abajo",
    la postura normal de reposo) a traves de cada pose en pose_paths, en
    orden, sosteniendo un momento en cada una -- y al final siempre vuelve
    exactamente a la posicion de la que partio antes de soltar el control,
    para no dejar un salto brusco ("totazo").

    Ejemplo para un saludo de mano con "meneo" real (usando 3 poses
    capturadas con motor_teleop.py que solo cambian la muneca):
        play_pose_sequence([pose1, pose2, pose3, pose1])

    Aborta (y vuelve a la posicion inicial) si el robot se inclina mas de
    la cuenta en cualquier momento de la reproduccion.
    """
    if not pose_paths:
        print("[robot_gestures] play_pose_sequence llamado sin poses, no hay nada que hacer.")
        return

    if not _gesture_lock.acquire(blocking=False):
        print("[robot_gestures] Gesto en curso, se ignora esta solicitud para no superponer movimientos.")
        return

    try:
        if not _is_robot_stable(network_interface):
            return

        try:
            target_poses = [_load_pose(path) for path in pose_paths]
        except (OSError, ValueError, KeyError) as error:
            print(f"[robot_gestures] No pude cargar una de las poses {pose_paths}: {error}")
            return

        _ensure_low_state_subscriber(network_interface)

        with _low_state_lock:
            state = _latest_low_state

        if state is None:
            print("[robot_gestures] No pude leer rt/lowstate para la pose, se omite por seguridad.")
            return

        baseline = {name: state.motor_state[POSE_JOINT_INDEX[name]].q for name in POSE_JOINTS}

        # Si se restringio a un subconjunto de articulaciones (p. ej. solo
        # brazo, sin cintura), las que quedan fuera se fijan al valor de
        # baseline en cada pose objetivo, en vez de al valor guardado en el
        # archivo -- asi no se mueven aunque la pose capturada las incluya.
        if joints_to_move is not None:
            locked_joints = [name for name in POSE_JOINTS if name not in joints_to_move]
            for target_pose in target_poses:
                for name in locked_joints:
                    target_pose[name] = baseline[name]

        publisher = _get_pose_publisher(network_interface)
        low_cmd = unitree_hg_msg_dds__LowCmd_()
        crc = CRC()

        def tilt_ok() -> bool:
            with _low_state_lock:
                current_state = _latest_low_state
            if current_state is None:
                return False
            roll, pitch, _yaw = current_state.imu_state.rpy
            return abs(roll) <= MAX_STABLE_TILT_RAD and abs(pitch) <= MAX_STABLE_TILT_RAD

        def interpolate(from_pose: dict, to_pose: dict, seconds: float) -> bool:
            """Devuelve False si tuvo que abortar por inestabilidad a mitad de camino."""
            steps = max(1, int(seconds / POSE_CONTROL_DT_SECONDS))
            for step in range(1, steps + 1):
                if not tilt_ok():
                    print("[robot_gestures] Inclinacion fuera de rango durante la pose, abortando.")
                    return False

                ratio = step / steps
                frame = {
                    name: from_pose[name] + (to_pose[name] - from_pose[name]) * ratio
                    for name in POSE_JOINTS
                }
                _publish_pose_frame(low_cmd, crc, publisher, 1.0, frame)
                time.sleep(POSE_CONTROL_DT_SECONDS)

            return True

        # Fase 1: ceder el control gradualmente, manteniendo la posicion actual.
        ramp_steps = max(1, int(POSE_RAMP_SECONDS / POSE_CONTROL_DT_SECONDS))
        for step in range(1, ramp_steps + 1):
            weight = step / ramp_steps
            _publish_pose_frame(low_cmd, crc, publisher, weight, baseline)
            time.sleep(POSE_CONTROL_DT_SECONDS)

        # Fase 2: recorrer cada pose de la secuencia, en orden.
        current_pose = baseline
        completed_ok = True

        for target_pose in target_poses:
            reached = interpolate(current_pose, target_pose, transition_seconds)

            if not reached:
                completed_ok = False
                break

            hold_steps = max(1, int(hold_seconds / POSE_CONTROL_DT_SECONDS))
            for _ in range(hold_steps):
                if not tilt_ok():
                    print("[robot_gestures] Inclinacion fuera de rango sosteniendo la pose, abortando.")
                    completed_ok = False
                    break
                _publish_pose_frame(low_cmd, crc, publisher, 1.0, target_pose)
                time.sleep(POSE_CONTROL_DT_SECONDS)

            if not completed_ok:
                break

            current_pose = target_pose

        # Fase 3: volver siempre a la posicion inicial exacta, sin importar si
        # se aborto a medio camino (parte desde donde realmente quedo el brazo).
        current = current_pose if completed_ok else _current_pose_estimate(current_pose)
        interpolate(current, baseline, transition_seconds)

        # Fase 4: liberar el control gradualmente, ya en la posicion inicial.
        for step in range(1, ramp_steps + 1):
            weight = 1.0 - (step / ramp_steps)
            _publish_pose_frame(low_cmd, crc, publisher, weight, baseline)
            time.sleep(POSE_CONTROL_DT_SECONDS)
    finally:
        _gesture_lock.release()


def play_saved_pose(
    pose_path: str,
    network_interface: str = "eth0",
    transition_seconds: float = POSE_TRANSITION_SECONDS,
    hold_seconds: float = POSE_HOLD_SECONDS,
) -> None:
    """Atajo para reproducir una sola pose (ver play_pose_sequence)."""
    play_pose_sequence(
        [pose_path],
        network_interface=network_interface,
        transition_seconds=transition_seconds,
        hold_seconds=hold_seconds,
    )

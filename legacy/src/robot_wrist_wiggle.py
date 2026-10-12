"""
Meneo de mano de bajo nivel, usado como complemento a la pose fija
"shake_hand" del G1ArmActionClient (que no oscila la mano).

Sigue el mismo patron de seguridad que el ejemplo oficial de Unitree
(example/g1/high_level/g1_arm7_sdk_dds_example.py): toma de control
gradual del brazo via "rt/arm_sdk" con una variable de "peso" (0 = control
normal del robot, 1 = control externo), rampa de entrada/salida suave, y
las mismas ganancias kp/kd que usa el ejemplo oficial.

A diferencia del ejemplo oficial, aqui SOLO se mueve el codo derecho, con
una oscilacion pequena (grados, no radianes grandes) alrededor de su
posicion actual -- el resto de las articulaciones del brazo se mantienen
fijas en su posicion actual mientras dura el meneo.
"""

import math
import time

from unitree_sdk2py.core.channel import ChannelPublisher, ChannelSubscriber
from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_, LowState_
from unitree_sdk2py.utils.crc import CRC

# Indices de articulacion del G1 (identicos a G1JointIndex en el ejemplo
# oficial example/g1/high_level/g1_arm7_sdk_dds_example.py).
LEFT_SHOULDER_PITCH = 15
LEFT_SHOULDER_ROLL = 16
LEFT_SHOULDER_YAW = 17
LEFT_ELBOW = 18
LEFT_WRIST_ROLL = 19
LEFT_WRIST_PITCH = 20
LEFT_WRIST_YAW = 21
RIGHT_SHOULDER_PITCH = 22
RIGHT_SHOULDER_ROLL = 23
RIGHT_SHOULDER_YAW = 24
RIGHT_ELBOW = 25
RIGHT_WRIST_ROLL = 26
RIGHT_WRIST_PITCH = 27
RIGHT_WRIST_YAW = 28
WAIST_YAW = 12
WAIST_ROLL = 13
WAIST_PITCH = 14
ARM_SDK_WEIGHT_JOINT = 29  # "kNotUsedJoint": 1 = arm_sdk tiene el control, 0 = control normal

# Solo se mantienen fijas (y bajo control de arm_sdk) las articulaciones del
# brazo derecho + cintura, que es lo unico relevante para "dar la mano".
# El brazo izquierdo no se toca -- se deja fuera de esta lista para no
# tomar control de el.
HELD_JOINTS = [
    RIGHT_SHOULDER_PITCH, RIGHT_SHOULDER_ROLL, RIGHT_SHOULDER_YAW,
    RIGHT_ELBOW, RIGHT_WRIST_ROLL, RIGHT_WRIST_PITCH, RIGHT_WRIST_YAW,
    WAIST_YAW, WAIST_ROLL, WAIST_PITCH,
]

OSCILLATING_JOINT = RIGHT_ELBOW

KP = 60.0
# kd mas alto que el del ejemplo oficial (1.5): esa ganancia estaba pensada
# para trayectorias lentas de varios segundos, no para oscilar. Con kd=1.5
# el primer intento de meneo salio muy brusco (velocidad angular alta con
# poco amortiguamiento). Con mas kd se resiste mejor el movimiento rapido.
KD = 3.0

CONTROL_DT_SECONDS = 0.02  # 50 Hz, igual que el ejemplo oficial

RAMP_SECONDS = 0.4
# Amplitud y velocidad reducidas frente al primer intento (8 grados a ~2.2 Hz,
# que resulto muy agresivo). Ahora: 5 grados a 1 Hz.
OSCILLATION_AMPLITUDE_RAD = math.radians(5)
OSCILLATION_CYCLES = 2
OSCILLATION_CYCLE_SECONDS = 1.0

_publisher = None
_subscriber = None
_latest_low_state = None


def _on_low_state(msg: LowState_) -> None:
    global _latest_low_state
    _latest_low_state = msg


def _ensure_io() -> None:
    global _publisher, _subscriber

    if _publisher is None:
        _publisher = ChannelPublisher("rt/arm_sdk", LowCmd_)
        _publisher.Init()

    if _subscriber is None:
        _subscriber = ChannelSubscriber("rt/lowstate", LowState_)
        _subscriber.Init(_on_low_state, 10)


def _wait_for_low_state(timeout_seconds: float = 2.0):
    waited = 0.0
    while _latest_low_state is None and waited < timeout_seconds:
        time.sleep(0.05)
        waited += 0.05
    return _latest_low_state


def _publish(low_cmd: LowCmd_, crc: CRC) -> None:
    low_cmd.crc = crc.Crc(low_cmd)
    _publisher.Write(low_cmd)


def wiggle_right_hand() -> bool:
    """
    Oscila levemente el codo derecho alrededor de su posicion actual,
    simulando el "meneo" de un saludo de mano. Pensado para llamarse justo
    despues de que el brazo ya este en la pose de "shake_hand".

    Devuelve True si pudo ejecutar el meneo, False si no pudo (por ejemplo,
    si no logro leer rt/lowstate a tiempo).
    """
    _ensure_io()

    state = _wait_for_low_state()
    if state is None:
        print("[robot_wrist_wiggle] No pude leer rt/lowstate, se omite el meneo.")
        return False

    baseline = {joint: state.motor_state[joint].q for joint in HELD_JOINTS}

    low_cmd = unitree_hg_msg_dds__LowCmd_()
    crc = CRC()

    def _write_frame(weight: float, elbow_offset: float = 0.0) -> None:
        low_cmd.motor_cmd[ARM_SDK_WEIGHT_JOINT].q = weight

        for joint in HELD_JOINTS:
            target = baseline[joint]
            if joint == OSCILLATING_JOINT:
                target += elbow_offset

            low_cmd.motor_cmd[joint].tau = 0.0
            low_cmd.motor_cmd[joint].q = target
            low_cmd.motor_cmd[joint].dq = 0.0
            low_cmd.motor_cmd[joint].kp = KP
            low_cmd.motor_cmd[joint].kd = KD

        _publish(low_cmd, crc)

    # Fase 1: ceder el control gradualmente al arm_sdk, manteniendo la pose actual.
    ramp_steps = max(1, int(RAMP_SECONDS / CONTROL_DT_SECONDS))
    for step in range(1, ramp_steps + 1):
        weight = step / ramp_steps
        _write_frame(weight)
        time.sleep(CONTROL_DT_SECONDS)

    # Fase 2: oscilar el codo derecho un numero entero de ciclos (termina en offset 0).
    total_oscillation_seconds = OSCILLATION_CYCLES * OSCILLATION_CYCLE_SECONDS
    oscillation_steps = max(1, int(total_oscillation_seconds / CONTROL_DT_SECONDS))
    for step in range(oscillation_steps):
        t = step * CONTROL_DT_SECONDS
        phase = 2 * math.pi * t / OSCILLATION_CYCLE_SECONDS
        offset = OSCILLATION_AMPLITUDE_RAD * math.sin(phase)
        _write_frame(1.0, offset)
        time.sleep(CONTROL_DT_SECONDS)

    # Fase 3: devolver el control gradualmente al robot, en la pose base (sin offset).
    for step in range(1, ramp_steps + 1):
        weight = 1.0 - (step / ramp_steps)
        _write_frame(weight)
        time.sleep(CONTROL_DT_SECONDS)

    return True

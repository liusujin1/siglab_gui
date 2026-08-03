"""
真机快速测试脚本 — 验证串口/RCI 通信是否正常。

用法:
  python _test_live.py COM3      # 默认 57600
  python _test_live.py COM5 115200
"""
from __future__ import annotations

import sys
import time

from python_samba.services.session import open_serial
from python_samba.protocol.commands import FilterStage


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 57600

    print(f"Connecting to {port} @ {baud}...")
    try:
        session = open_serial(port, baud, readonly=True)
        version = session.open()
        print(f"✅ Connected! Firmware: {version}")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return 1

    # 1. Read loop status
    try:
        loop = session.get_loop_status()
        print(f"✅ Loop status: individual=0x{loop.individual:X} system=0x{loop.system:X}")
    except Exception as e:
        print(f"❌ Loop status: {e}")

    # 2. Read sample frequency
    try:
        fs = session.get_sample_frequency()
        print(f"✅ Sample frequency: {fs} Hz")
    except Exception as e:
        print(f"❌ Sample frequency: {e}")

    # 3. Read velocity filter (axis 0, stage 0)
    try:
        fs = session.get_velocity_filter(0, 0)
        print(f"✅ Velocity filter [0,0]: type={fs.filter_type} ({fs.type_name}) params={fs.params}")
    except Exception as e:
        print(f"❌ Velocity filter: {e}")

    # 4. Read velocity sensor matrix (axis 0)
    try:
        sens = session.get_velocity_sensor_matrix(0)
        print(f"✅ Velocity sensor matrix [0]: {[f'{x:.3f}' for x in sens]}")
    except Exception as e:
        print(f"❌ Velocity sensor matrix: {e}")

    # 5. Read velocity motor matrix (axis 0)
    try:
        motor = session.get_velocity_motor_matrix(0)
        print(f"✅ Velocity motor matrix [0]: {[f'{x:.3f}' for x in motor]}")
    except Exception as e:
        print(f"❌ Velocity motor matrix: {e}")

    # 6. Read proximity filter (axis 0, stage 0)
    try:
        pf = session.get_proximity_filter(0, 0)
        print(f"✅ Proximity filter [0,0]: type={pf.filter_type} ({pf.type_name}) params={pf.params}")
    except Exception as e:
        print(f"❌ Proximity filter: {e}")

    # 7. Read proximity offsets
    try:
        off = session.get_proximity_offsets()
        print(f"✅ Proximity offsets: {[f'{x:.1f}' for x in off]}")
    except Exception as e:
        print(f"❌ Proximity offsets: {e}")

    # 8. Read geophone inputs
    try:
        geo = session.get_geophone_inputs()
        print(f"✅ Geophone inputs: {geo}")
    except Exception as e:
        print(f"❌ Geophone inputs: {e}")

    # 9. Read output limit
    try:
        opl = session.get_output_limit()
        print(f"✅ Output limit: {opl}%")
    except Exception as e:
        print(f"❌ Output limit: {e}")

    # 10. Read noise type
    try:
        nt = session.get_noise_type()
        print(f"✅ Noise type: {nt}")
    except Exception as e:
        print(f"❌ Noise type: {e}")

    # 11. Read FF filter (source 0, stage 0)
    try:
        ff = session.get_ff_filter(0, 0)
        print(f"✅ FF filter [0,0]: type={ff.filter_type} ({ff.type_name}) params={ff.params}")
    except Exception as e:
        print(f"❌ FF filter: {e}")

    # 12. Read pneumatic filter (axis 0, stage 0) — may fail if no pneumatic
    try:
        pn = session.get_pneumatic_filter(0, 0)
        print(f"✅ Pneumatic filter [0,0]: type={pn.filter_type} params={pn.params}")
    except Exception as e:
        print(f"ℹ️ Pneumatic filter: {e} (normal if no pneumatic hardware)")

    # 13. Read ADC sequence
    try:
        adc = session.get_adc_sequence()
        print(f"✅ ADC sequence: {adc[:5]}... ({len(adc)} channels)")
    except Exception as e:
        print(f"❌ ADC sequence: {e}")

    # 14. Read DAC sequence
    try:
        dac = session.get_dac_sequence()
        print(f"✅ DAC sequence: {dac[:5]}... ({len(dac)} channels)")
    except Exception as e:
        print(f"❌ DAC sequence: {e}")

    # 15. Read switch status
    try:
        sw = session.get_switch_status()
        print(f"✅ Switch status: {sw}")
    except Exception as e:
        print(f"❌ Switch status: {e}")

    # 16. Try reading all 6×7 velocity filters
    vel_ok = 0
    for ax in range(6):
        for st in range(7):
            try:
                session.get_velocity_filter(ax, st)
                vel_ok += 1
            except Exception:
                pass
    print(f"✅ Velocity filters read: {vel_ok}/42")

    # 17. Try reading all 6×4 proximity filters
    prox_ok = 0
    for ax in range(6):
        for st in range(4):
            try:
                session.get_proximity_filter(ax, st)
                prox_ok += 1
            except Exception:
                pass
    print(f"✅ Proximity filters read: {prox_ok}/24")

    session.close()
    print(f"\n✅ All tests completed! Connection to {port} was successful.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
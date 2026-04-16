"""
Test 1: Can pycaw detect microphone (capture) sessions?
Monitors mic peak level for 30 seconds. Prints status every 5s.
"""
import time
import comtypes
from ctypes import POINTER
from comtypes import CLSCTX_ALL, GUID
from pycaw.pycaw import IAudioMeterInformation

CLSID_MMDeviceEnumerator = GUID('{BCDE0395-E52F-467C-8E3D-C4579291692E}')
IID_IMMDeviceEnumerator = GUID('{A95664D2-9614-4F35-A746-DE8DB63617E6}')


class IMMDevice(comtypes.IUnknown):
    _iid_ = GUID('{D666063F-1587-4E43-81F1-B948E807363F}')
    _methods_ = [
        comtypes.COMMETHOD([], comtypes.HRESULT, 'Activate',
            (['in'], POINTER(GUID), 'iid'),
            (['in'], comtypes.c_ulong, 'dwClsCtx'),
            (['in'], POINTER(comtypes.c_ulong), 'pActivationParams'),
            (['out', 'retval'], POINTER(POINTER(comtypes.IUnknown)), 'ppInterface')),
    ]


class IMMDeviceEnumerator(comtypes.IUnknown):
    _iid_ = IID_IMMDeviceEnumerator
    _methods_ = [
        comtypes.COMMETHOD([], comtypes.HRESULT, 'EnumAudioEndpoints',
            (['in'], comtypes.c_uint, 'dataFlow'),
            (['in'], comtypes.c_ulong, 'dwStateMask'),
            (['out', 'retval'], POINTER(POINTER(comtypes.IUnknown)), 'ppDevices')),
        comtypes.COMMETHOD([], comtypes.HRESULT, 'GetDefaultAudioEndpoint',
            (['in'], comtypes.c_uint, 'dataFlow'),
            (['in'], comtypes.c_uint, 'role'),
            (['out', 'retval'], POINTER(POINTER(IMMDevice)), 'ppEndpoint')),
    ]


def get_mic_peak():
    """Get peak audio level from default capture (mic) device."""
    enumerator = comtypes.CoCreateInstance(
        CLSID_MMDeviceEnumerator,
        IMMDeviceEnumerator,
        CLSCTX_ALL
    )
    device = enumerator.GetDefaultAudioEndpoint(1, 0)  # eCapture, eConsole
    unk = device.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
    meter = unk.QueryInterface(IAudioMeterInformation)
    return meter.GetPeakValue()


if __name__ == "__main__":
    print("=== Mic Detection Test ===")
    print("Monitoring for 30 seconds. Talk into your mic!\n")

    # First: test single read
    try:
        peak = get_mic_peak()
        print(f"[OK] Initial read: peak={peak:.6f}")
    except Exception as e:
        print(f"[FAIL] Cannot read mic: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        exit(1)

    # Monitor loop
    was_active = False
    last_print = 0
    start = time.time()

    while time.time() - start < 30:
        try:
            peak = get_mic_peak()
            is_active = peak > 0.005
            now = time.time()

            # Always print every 5 seconds
            if now - last_print >= 5 or (is_active != was_active):
                state = "ACTIVE" if is_active else "silent"
                marker = ">>>" if is_active != was_active else "   "
                print(f"{marker} [{time.strftime('%H:%M:%S')}] {state:7s} peak={peak:.6f}")
                last_print = now

            was_active = is_active
        except Exception as e:
            print(f"ERR [{time.strftime('%H:%M:%S')}] {type(e).__name__}: {e}")
        time.sleep(0.3)

    print("\n=== Done ===")

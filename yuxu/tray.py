"""Windows notification-area entry point for the local web service."""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
from pathlib import Path
import sys
import threading
import webbrowser

from .store import runtime_dir

WM_DESTROY, WM_CLOSE, WM_COMMAND = 0x0002, 0x0010, 0x0111
WM_CONTEXTMENU, WM_RBUTTONUP = 0x007B, 0x0205
WM_DPICHANGED = 0x02E0
TRAY_CALLBACK = 0x8001
OPEN_PAGE = 1
EXIT_APP = 2
ICON_ID = 1
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_SHOWTIP = 1, 2, 4, 0x80
SM_CXSMICON, SM_CYSMICON = 49, 50
WINDOW_PROC = ct.WINFUNCTYPE(ct.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WindowClass(ct.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WINDOW_PROC), ("cbClsExtra", ct.c_int),
        ("cbWndExtra", ct.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class NotifyIcon(ct.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD), ("guidItem", ct.c_ubyte * 16), ("hBalloonIcon", wt.HICON),
    ]


def _bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


def _taskbar_icon_size(user) -> tuple[int, int]:
    if hasattr(user, "GetDpiForWindow") and hasattr(user, "GetSystemMetricsForDpi"):
        find_window = _bind(user, "FindWindowW", wt.HWND, wt.LPCWSTR, wt.LPCWSTR)
        taskbar = find_window("Shell_TrayWnd", None)
        if taskbar:
            dpi = _bind(user, "GetDpiForWindow", wt.UINT, wt.HWND)(taskbar)
            if dpi:
                metric = _bind(user, "GetSystemMetricsForDpi", ct.c_int, ct.c_int, wt.UINT)
                return metric(SM_CXSMICON, dpi), metric(SM_CYSMICON, dpi)
    return user.GetSystemMetrics(SM_CXSMICON), user.GetSystemMetrics(SM_CYSMICON)


def _watch_service(window, post_message, kernel) -> None:
    try:
        pid = int((runtime_dir() / "service.pid").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    open_process = _bind(kernel, "OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
    wait = _bind(kernel, "WaitForSingleObject", wt.DWORD, wt.HANDLE, wt.DWORD)
    handle = open_process(0x00100000, False, pid)  # SYNCHRONIZE
    if handle:
        try:
            if wait(handle, 0xFFFFFFFF) == 0:  # INFINITE, WAIT_OBJECT_0
                post_message(window, WM_CLOSE, 0, 0)
        finally:
            kernel.CloseHandle(handle)


def run(port: int) -> None:
    user = ct.WinDLL("user32", use_last_error=True)
    if hasattr(user, "SetProcessDpiAwarenessContext"):
        set_dpi_awareness = _bind(user, "SetProcessDpiAwarenessContext", wt.BOOL, wt.HANDLE)
        set_dpi_awareness(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    shell = ct.WinDLL("shell32", use_last_error=True)
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    create_mutex = _bind(kernel, "CreateMutexW", wt.HANDLE, ct.c_void_p, wt.BOOL, wt.LPCWSTR)
    _bind(kernel, "CloseHandle", wt.BOOL, wt.HANDLE)
    mutex = create_mutex(None, False, f"Local\\YuXuTray-{port}")
    already_running = ct.get_last_error() == 183  # ERROR_ALREADY_EXISTS
    if not mutex:
        raise ct.WinError(ct.get_last_error())
    try:
        if not already_running:
            _message_loop(port, user, shell, kernel)
    finally:
        kernel.CloseHandle(mutex)


def _message_loop(port: int, user, shell, kernel) -> None:
    url = f"http://127.0.0.1:{port}/"
    class_name = f"YuXuTray-{port}"
    module = _bind(kernel, "GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)(None)
    notify = _bind(shell, "Shell_NotifyIconW", wt.BOOL, wt.DWORD, ct.POINTER(NotifyIcon))
    default_proc = _bind(user, "DefWindowProcW", ct.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    post_message = _bind(user, "PostMessageW", wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    taskbar_created = _bind(user, "RegisterWindowMessageW", wt.UINT, wt.LPCWSTR)("TaskbarCreated")
    get_message = _bind(user, "GetMessageW", wt.BOOL, ct.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT)
    dispatch_message = _bind(user, "DispatchMessageW", ct.c_ssize_t, ct.POINTER(wt.MSG))
    destroy_window = _bind(user, "DestroyWindow", wt.BOOL, wt.HWND)
    post_quit = _bind(user, "PostQuitMessage", None, ct.c_int)
    icon_data = NotifyIcon(cbSize=ct.sizeof(NotifyIcon), uID=ICON_ID, uCallbackMessage=TRAY_CALLBACK, szTip="余序")
    icon_path = Path(__file__).resolve().parent.parent / "assets" / "yuxu-tray.ico"
    load_image = _bind(user, "LoadImageW", wt.HANDLE, wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ct.c_int, ct.c_int, wt.UINT)
    destroy_icon = _bind(user, "DestroyIcon", wt.BOOL, wt.HICON)
    icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP

    def refresh_icon(operation: int) -> None:
        width, height = _taskbar_icon_size(user)
        icon = load_image(None, str(icon_path), 1, width, height, 0x10)
        if not icon:
            return
        previous = icon_data.hIcon
        icon_data.hIcon = icon
        updated = notify(operation, ct.byref(icon_data))
        if updated and operation == NIM_ADD:
            icon_data.uVersion = 4
            notify(NIM_SETVERSION, ct.byref(icon_data))
        if previous:
            destroy_icon(previous)

    @WINDOW_PROC
    def window_proc(hwnd, message, wparam, lparam):
        if message == TRAY_CALLBACK and lparam & 0xFFFF in (WM_CONTEXTMENU, WM_RBUTTONUP):
            _show_menu(user, hwnd, post_message)
            return 0
        if message == WM_COMMAND and wparam & 0xFFFF == OPEN_PAGE:
            webbrowser.open(url)
            return 0
        if message == WM_COMMAND and wparam & 0xFFFF == EXIT_APP:
            from . import server

            try:
                server.stop()
            except (OSError, RuntimeError, ValueError) as error:
                message_box = _bind(user, "MessageBoxW", ct.c_int, wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT)
                message_box(hwnd, f"暂时无法退出余序。\n{error}", "余序", 0x30)
            else:
                post_message(hwnd, WM_CLOSE, 0, 0)
            return 0
        if message == WM_DESTROY:
            notify(NIM_DELETE, ct.byref(icon_data))
            post_quit(0)
            return 0
        if taskbar_created and message == taskbar_created:
            refresh_icon(NIM_ADD)
            return 0
        if message == WM_DPICHANGED and icon_data.hWnd:
            refresh_icon(NIM_MODIFY)
            return 0
        return default_proc(hwnd, message, wparam, lparam)

    register_class = _bind(user, "RegisterClassW", wt.WORD, ct.POINTER(WindowClass))
    create_window = _bind(
        user, "CreateWindowExW", wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ct.c_void_p,
    )
    window_class = WindowClass(lpfnWndProc=window_proc, hInstance=module, lpszClassName=class_name)
    try:
        if not register_class(ct.byref(window_class)):
            raise ct.WinError(ct.get_last_error())
        icon_data.hWnd = create_window(0, class_name, "余序", 0, 0, 0, 0, 0, None, None, module, None)
        if not icon_data.hWnd:
            raise ct.WinError(ct.get_last_error())
        refresh_icon(NIM_ADD)
        if not icon_data.hIcon:
            raise ct.WinError(ct.get_last_error())
        threading.Thread(target=_watch_service, args=(icon_data.hWnd, post_message, kernel), daemon=True).start()
        message = wt.MSG()
        while get_message(ct.byref(message), None, 0, 0) > 0:
            dispatch_message(ct.byref(message))
    finally:
        if icon_data.hWnd:
            destroy_window(icon_data.hWnd)
        if icon_data.hIcon:
            destroy_icon(icon_data.hIcon)


def _show_menu(user, window, post_message) -> None:
    create_menu = _bind(user, "CreatePopupMenu", wt.HMENU)
    destroy_menu = _bind(user, "DestroyMenu", wt.BOOL, wt.HMENU)
    append_menu = _bind(user, "AppendMenuW", wt.BOOL, wt.HMENU, wt.UINT, ct.c_size_t, wt.LPCWSTR)
    track_menu = _bind(
        user, "TrackPopupMenu", wt.BOOL, wt.HMENU, wt.UINT,
        ct.c_int, ct.c_int, ct.c_int, wt.HWND, ct.c_void_p,
    )
    foreground = _bind(user, "SetForegroundWindow", wt.BOOL, wt.HWND)
    get_cursor = _bind(user, "GetCursorPos", wt.BOOL, ct.POINTER(wt.POINT))
    menu = create_menu()
    if not menu:
        return
    try:
        point = wt.POINT()
        append_menu(menu, 0, OPEN_PAGE, "打开网页")
        append_menu(menu, 0, EXIT_APP, "退出余序")
        get_cursor(ct.byref(point))
        foreground(window)
        track_menu(menu, 2, point.x, point.y, 0, window, None)  # TPM_RIGHTBUTTON
        post_message(window, 0, 0, 0)  # WM_NULL dismisses the menu cleanly.
    finally:
        destroy_menu(menu)


if __name__ == "__main__":
    run(int(sys.argv[1]))

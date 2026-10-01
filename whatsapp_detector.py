"""
================================================================================
                    WHATSAPP NOTIFICATION MONITOR & RULE MATCHER
================================================================================

PREREQUISITES & INSTALLATION:
-----------------------------
1. Operating System:
   - Windows 10 (Version 1607+ / Build 14393+) or Windows 11.
   - Standard Windows Notification permissions enabled in:
     Windows Settings -> Privacy & security -> Notifications ("Notification Access")

2. Required Python Packages:
   Run the following command to install the required libraries:
       pip install winsdk colorama

   (Alternative for PyWinRT):
       pip install winrt-Windows.UI.Notifications.Management winrt-Windows.UI.Notifications winrt-Windows.Data.Xml.Dom colorama

================================================================================
"""

import sys
import os
import asyncio
import winsound
import unicodedata
import re
from datetime import datetime

# Check Windows platform
if sys.platform != "win32":
    sys.exit("[-] Error: This script requires Windows 10 or Windows 11.")

# Try importing WinRT packages (supports both winsdk and winrt-Windows.* namespaces)
try:
    import winsdk.windows.ui.notifications.management as win_mgmt
    import winsdk.windows.ui.notifications as win_notif
    import winsdk.windows.data.xml.dom as win_xml
    WINRT_FLAVOR = "winsdk"
except ImportError:
    try:
        import winrt.windows.ui.notifications.management as win_mgmt
        import winrt.windows.ui.notifications as win_notif
        import winrt.windows.data.xml.dom as win_xml
        WINRT_FLAVOR = "winrt"
    except ImportError:
        sys.exit(
            "[-] Error: Required Windows Runtime libraries not found.\n"
            "    Please run: pip install winsdk colorama\n"
            "    or: pip install winrt-Windows.UI.Notifications.Management winrt-Windows.UI.Notifications colorama"
        )

# Terminal color formatting
try:
    import colorama
    from colorama import Fore, Style, Back
    colorama.init(autoreset=True)
    COLOR_ENABLED = True
except ImportError:
    COLOR_ENABLED = False
    class Fore:
        CYAN = YELLOW = RED = GREEN = MAGENTA = WHITE = BLUE = RESET = ""
    class Style:
        BRIGHT = RESET_ALL = ""
    class Back:
        RESET = ""


# ==============================================================================
#                               CONFIGURABLE RULES
# ==============================================================================
# Add, remove, or modify rules in this list:
#   - "type": Identifier for the rule (displayed in terminal and notifications).
#   - "mode":
#       * "all" : Every keyword in the list must appear in the notification text.
#       * "any" : At least one keyword in the list must appear in the notification text.
#   - "keywords": List of keywords to match.
#                 COMPLETELY CASE-INSENSITIVE: "AmAzon", "AMAZON", "amazon" all match
#                 regardless of whether the message has small, capital, or mixed letters!
#   - "sound"   : (Optional) Specific .wav sound filename in C:\Windows\Media\ to play.
#                 If omitted, each rule automatically gets its own distinct sound!
# ==============================================================================
RULES = [
    {
        "type": "java_html",
        "mode": "all",  # all keywords must be present
        "keywords": ["java", "html"],
        "sound": "Windows Notify Messaging.wav",  # Messenger chime
    },
    {
        "type": "javascript_family",
        "mode": "any",  # any one keyword is enough
        "keywords": ["react", "javascript", "js"],
        "sound": "notify.wav",  # Water drop pop
    },
    {
        "type": "urgent",
        "mode": "any",
        "keywords": ["urgent"],
        "sound": "Alarm01.wav",  # Loud alarm tone
    },
    {
        "type": "Reqrments",
        "mode": "any",  # "any" catches jobs matching any of these; use "all" if all required
        "keywords": ["APIs", "EDI", "Manhattan", "Remote", "Exp: 5+Years", "5+ years"],
        "sound": "Ring01.wav",  # Phone ring chime for job requirements
    },
    {
        "type": "payment",
        "mode": "any",
        "keywords": ["payment"],
        "sound": "chimes.wav",  # Ascending cash/chime
    },
    {
        "type": "AMAZON",
        "mode": "any",
        # Case-insensitive: 'AmAzon', 'AMAZON', 'amazon' will match any message!
        "keywords": ["AMAZON"],
        "sound": "tada.wav",  # Fanfare celebration sound for Amazon deals!
    }
]

# ==============================================================================
#                             GENERAL SETTINGS
# ==============================================================================
# Process notifications already waiting in the Windows Action Center on startup
PROCESS_EXISTING_ON_STARTUP = True

# Sound alert toggle
ENABLE_SOUND = True

# Desktop toast alert toggle
ENABLE_DESKTOP_TOAST = False

# Polling interval in seconds (used as fallback when event sink requires UWP package identity)
POLL_INTERVAL_SECONDS = 1.0


# Color palette for terminal logs per rule type
COLOR_PALETTE = [
    Fore.CYAN,
    Fore.YELLOW,
    Fore.RED,
    Fore.MAGENTA,
    Fore.GREEN,
    Fore.LIGHTBLUE_EX,
    Fore.LIGHTGREEN_EX,
    Fore.LIGHTCYAN_EX,
    Fore.LIGHTYELLOW_EX,
    Fore.LIGHTMAGENTA_EX,
]

TYPE_COLORS = {
    "java_html": Fore.CYAN,
    "javascript_family": Fore.YELLOW,
    "urgent": Fore.RED,
    "payment": Fore.GREEN,
    "amazon": Fore.MAGENTA,
    "reqrments": Fore.LIGHTBLUE_EX,
}
DEFAULT_TYPE_COLOR = Fore.MAGENTA


def get_color_for_rule(rule: dict, index: int = 0):
    """Returns a unique terminal color for any rule."""
    rule_type = str(rule.get("type", "")).strip().lower()
    if rule_type in TYPE_COLORS:
        return TYPE_COLORS[rule_type]
    return COLOR_PALETTE[index % len(COLOR_PALETTE)]


# ==============================================================================
#                           CORE RULE MATCHING LOGIC
# ==============================================================================
def normalize_text(text: str) -> str:
    """
    Normalizes text for 100% robust case-insensitive matching:
    - Normalizes unicode forms (NFKC)
    - Strips WhatsApp's hidden zero-width and directional formatting chars (\u200e, \u200f, etc.)
    - Collapses multiple whitespace
    - Converts to lowercase
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    # Remove zero-width spaces, directional marks, and non-breaking spaces inserted by WhatsApp
    for ch in ("\u200e", "\u200f", "\u200b", "\u200c", "\u200d", "\ufeff", "\xa0"):
        text = text.replace(ch, " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def match_rules(text: str, rules: list[dict]) -> list[dict]:
    """
    Evaluates text against a list of rule definitions with complete case-insensitivity:
    - Normalizes WhatsApp formatting, zero-width characters, and Unicode.
    - Matches keywords regardless of letter casing (e.g. 'AmAzon', 'AMAZON', 'amazon' all match).
    - If mode == 'all': all keywords must appear somewhere in the text.
    - If mode == 'any': at least one keyword must appear in the text.
    - Substring matching: 'amazon' matches 'Amazon deals', 'AMAZON.COM', etc.
    """
    if not text:
        return []
    
    clean_text = normalize_text(text)
    matched = []
    
    for rule in rules:
        mode = rule.get("mode", "any").strip().lower()
        raw_keywords = rule.get("keywords", [])
        keywords = [normalize_text(k) for k in raw_keywords if k and normalize_text(k)]
        
        if not keywords:
            continue
            
        if mode == "all":
            # All keywords must be present in clean_text
            if all(kw in clean_text for kw in keywords):
                matched.append(rule)
        elif mode == "any":
            # At least one keyword must be present in clean_text
            if any(kw in clean_text for kw in keywords):
                matched.append(rule)
        else:
            if any(kw in clean_text for kw in keywords):
                matched.append(rule)
                
    return matched


# ==============================================================================
#                      NOTIFICATION PARSING & EXTRACTION
# ==============================================================================
def extract_notification_content(notif) -> tuple[str, str, list[str]]:
    """
    Extracts the title, body, and raw text elements from a UserNotification object.
    
    Returns:
        (title, body, raw_texts)
    """
    raw_texts: list[str] = []
    try:
        if notif.notification and notif.notification.visual:
            for binding in notif.notification.visual.bindings:
                for text_elem in binding.get_text_elements():
                    if text_elem and text_elem.text:
                        raw_texts.append(text_elem.text.strip())
    except Exception as e:
        # Visual binding read error
        pass

    if not raw_texts:
        return "", "", []

    # For native WhatsApp desktop app:
    # Element 0 is the sender/group name (Title)
    # Element 1+ is the message content (Body)
    if len(raw_texts) == 1:
        title = raw_texts[0]
        body = ""
    else:
        title = raw_texts[0]
        body = "\n".join(raw_texts[1:])
        
    return title, body, raw_texts


def is_whatsapp_notification(notif, app_name: str, raw_texts: list[str]) -> bool:
    """
    Returns True ONLY if the notification originates from the native WhatsApp Desktop / Store app.
    Explicitly ignores and excludes Phone Link (Microsoft Phone Link / Link to Windows).
    """
    app_name_lower = (app_name or "").lower()
    
    # 1. Explicitly ignore Microsoft Phone Link
    if "phone link" in app_name_lower or "your phone" in app_name_lower:
        return False

    # 2. Check Package ID / AppUserModelId to verify it is NOT Phone Link
    app_id = ""
    try:
        if notif.app_info and hasattr(notif.app_info, "id") and notif.app_info.id:
            app_id = str(notif.app_info.id).lower()
    except (Exception, OSError, BaseException):
        pass

    if "phonelink" in app_id or "yourphone" in app_id:
        return False

    # 3. Match ONLY native WhatsApp Desktop app:
    # - Display Name is "WhatsApp" / "WhatsApp Desktop" / "WhatsApp Beta"
    # - Or Package ID contains "whatsapp" (e.g. 5319275A.WhatsAppDesktop)
    if "whatsapp" in app_name_lower or "whatsapp" in app_id:
        return True

    return False


# ==============================================================================
#                           ACTION HANDLERS PER TYPE
# ==============================================================================
# System wav audio files in C:\Windows\Media (routes directly to active speakers/headphones)
MEDIA_DIR = r"C:\Windows\Media"

# Distinct presets for known rule types
SOUND_PRESETS = {
    "urgent": os.path.join(MEDIA_DIR, "Alarm01.wav"),                  # High siren alarm
    "payment": os.path.join(MEDIA_DIR, "chimes.wav"),                   # Melodic ascending chimes
    "amazon": os.path.join(MEDIA_DIR, "tada.wav"),                      # Fanfare / Celebration
    "reqrments": os.path.join(MEDIA_DIR, "Ring01.wav"),                 # Phone ring chime
    "java_html": os.path.join(MEDIA_DIR, "Windows Notify Messaging.wav"), # Modern messenger chime
    "javascript_family": os.path.join(MEDIA_DIR, "notify.wav"),         # Crisp water drop pop
}

# Pool of distinct sounds to automatically assign to any new rule
DISTINCT_SOUND_POOL = [
    os.path.join(MEDIA_DIR, "tada.wav"),                      # 1. Trumpet celebration (tada)
    os.path.join(MEDIA_DIR, "Ring01.wav"),                    # 2. Ring chime
    os.path.join(MEDIA_DIR, "chimes.wav"),                    # 3. Sparkling chimes
    os.path.join(MEDIA_DIR, "Alarm01.wav"),                   # 4. Urgent alarm
    os.path.join(MEDIA_DIR, "chord.wav"),                     # 5. Deep chord
    os.path.join(MEDIA_DIR, "ding.wav"),                      # 6. Bell ding
    os.path.join(MEDIA_DIR, "Windows Foreground.wav"),        # 7. High foreground bell
    os.path.join(MEDIA_DIR, "Ring02.wav"),                    # 8. Modern phone tone
    os.path.join(MEDIA_DIR, "Windows Background.wav"),        # 9. Low warm chime
    os.path.join(MEDIA_DIR, "Alarm02.wav"),                   # 10. Secondary alarm
    os.path.join(MEDIA_DIR, "Windows Notify Messaging.wav"),  # 11. Messenger chime
    os.path.join(MEDIA_DIR, "notify.wav"),                    # 12. Classic notify
]
DEFAULT_SOUND = os.path.join(MEDIA_DIR, "Windows Notify.wav")


def get_sound_for_rule(rule: dict, index: int = 0) -> str:
    """
    Resolves the exact WAV sound file for a given rule:
    1. If the rule explicitly defines "sound": "tada.wav" or a full path, use that.
    2. Else if rule type matches a known preset (e.g. amazon -> tada.wav, reqrments -> Ring01.wav).
    3. Else dynamically pick a unique sound from the Windows Media sound pool so every rule sounds distinct.
    """
    custom_sound = rule.get("sound")
    if custom_sound:
        if os.path.isabs(custom_sound) and os.path.exists(custom_sound):
            return custom_sound
        media_path = os.path.join(MEDIA_DIR, custom_sound)
        if not media_path.lower().endswith(".wav"):
            media_path += ".wav"
        if os.path.exists(media_path):
            return media_path

    rule_type = str(rule.get("type", "")).strip().lower()
    if rule_type in SOUND_PRESETS and os.path.exists(SOUND_PRESETS[rule_type]):
        return SOUND_PRESETS[rule_type]

    pool_path = DISTINCT_SOUND_POOL[index % len(DISTINCT_SOUND_POOL)]
    if os.path.exists(pool_path):
        return pool_path

    return DEFAULT_SOUND


def play_sound(sound_path: str):
    """
    Plays a WAV audio file directly through your active speakers / headphones.
    Uses SND_ASYNC so the monitoring loop never blocks.
    """
    if not ENABLE_SOUND:
        return
        
    try:
        if sound_path and os.path.exists(sound_path):
            winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        try:
            winsound.MessageBeep(-1)
        except Exception:
            pass


def play_sound_for_type(rule_or_type):
    """Backwards-compatible helper to play sound for a rule dict or rule type string."""
    if isinstance(rule_or_type, dict):
        sound_path = get_sound_for_rule(rule_or_type)
    else:
        sound_path = SOUND_PRESETS.get(str(rule_or_type).lower(), DEFAULT_SOUND)
    play_sound(sound_path)


def show_desktop_toast(rule_type: str, title: str, body: str):
    """
    Displays a native Windows toast notification for the matched rule.
    """
    if not ENABLE_DESKTOP_TOAST:
        return
        
    try:
        template = win_notif.ToastNotificationManager.get_template_content(
            win_notif.ToastTemplateType.TOAST_TEXT02
        )
        texts = template.get_elements_by_tag_name("text")
        if texts.length >= 2:
            texts[0].append_child(template.create_text_node(f"[{rule_type.upper()}] {title}"))
            preview_body = (body[:120] + "...") if len(body) > 120 else body
            texts[1].append_child(template.create_text_node(preview_body))
            
            toast = win_notif.ToastNotification(template)
            toast.tag = rule_type
            notifier = win_notif.ToastNotificationManager.create_toast_notifier("WhatsApp Rule Matcher")
            notifier.show(toast)
    except Exception:
        pass


def handle_matched_rule(rule: dict, title: str, body: str, notif_id: int):
    """
    Dispatches actions when a rule matches:
      1. Colored console log
      2. Unique audio alert per rule
      3. Desktop toast
      4. Extensible hook for webhooks (Discord / Slack / Telegram)
    """
    rule_type = str(rule.get("type", "unknown"))
    color = get_color_for_rule(rule)
    sound_path = get_sound_for_rule(rule)
    sound_name = os.path.basename(sound_path)
    
    timestamp = datetime.now().strftime("%H:%M:%S")
    header_tag = f"[{rule_type.upper()}]"
    
    # 1. Colored terminal log
    print(
        f"{Fore.WHITE}[{timestamp}]{Style.RESET_ALL} "
        f"{color}{Style.BRIGHT}{header_tag}{Style.RESET_ALL} "
        f"{Fore.WHITE}{Style.BRIGHT}{title}:{Style.RESET_ALL} "
        f"{body.replace(chr(10), ' ')}"
    )
    print(f"  {Fore.CYAN}🔊 Sound played: {sound_name}{Style.RESET_ALL}")
    
    # 2. Sound playback
    play_sound(sound_path)
    
    # 3. Custom desktop toast
    show_desktop_toast(rule_type, title, body)


# ==============================================================================
#                     NOTIFICATION LISTENER & EVENT LOOP
# ==============================================================================
class WhatsAppNotificationWatcher:
    """
    Manages UserNotificationListener, requests user permissions,
    processes existing notifications, and continuously listens for incoming events.
    """
    
    def __init__(self, rules: list[dict]):
        self.rules = rules
        self.listener = None
        self.seen_notification_ids: set[int] = set()
        self.is_running = False
        self.event_token = None

    async def initialize(self) -> bool:
        """
        Retrieves UserNotificationListener instance and checks/requests permissions.
        """
        print(f"{Fore.CYAN}[*] Initializing Windows UserNotificationListener ({WINRT_FLAVOR})...{Style.RESET_ALL}")
        
        try:
            self.listener = win_mgmt.UserNotificationListener.current
        except Exception as e:
            print(f"{Fore.RED}[-] Failed to access UserNotificationListener: {e}{Style.RESET_ALL}")
            print("    Make sure you are on Windows 10 (1607+) or Windows 11.")
            return False

        if self.listener is None:
            print(f"{Fore.RED}[-] UserNotificationListener is not available on this platform.{Style.RESET_ALL}")
            return False

        # Request user notification access
        try:
            print(f"{Fore.CYAN}[*] Requesting notification access permission...{Style.RESET_ALL}")
            access_status = await self.listener.request_access_async()
            
            # Access status enum: 1 = ALLOWED, 2 = DENIED, 0 = UNSPECIFIED
            allowed = win_mgmt.UserNotificationListenerAccessStatus.ALLOWED
            if access_status != allowed:
                print(f"{Fore.RED}[-] Notification access denied (status: {access_status}).{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}[!] To fix this:{Style.RESET_ALL}")
                print("    1. Open Windows Settings -> Privacy & security -> Notifications.")
                print("    2. Ensure 'Notification access' is turned ON.")
                print("    3. Allow apps to access your notifications.")
                return False
                
            print(f"{Fore.GREEN}[+] Notification access granted successfully!{Style.RESET_ALL}")
            return True
            
        except Exception as e:
            print(f"{Fore.RED}[-] Error requesting notification access: {e}{Style.RESET_ALL}")
            return False

    def process_single_notification(self, notif) -> bool:
        """
        Extracts details from a notification, checks WhatsApp origin,
        and evaluates against configured rules.
        """
        try:
            notif_id = notif.id
            if notif_id in self.seen_notification_ids:
                return False
            self.seen_notification_ids.add(notif_id)

            # Retrieve App Display Name
            app_name = ""
            try:
                if notif.app_info and notif.app_info.display_info:
                    app_name = notif.app_info.display_info.display_name or ""
            except (Exception, OSError, BaseException):
                pass

            # Extract title and body
            title, body, raw_texts = extract_notification_content(notif)

            # Filter only WhatsApp notifications
            if not is_whatsapp_notification(notif, app_name, raw_texts):
                return False

            # Combine title and body for full search
            combined_text = f"{title} {body}"
            
            # Run rule matching
            matches = match_rules(combined_text, self.rules)
            
            for rule in matches:
                handle_matched_rule(rule, title, body, notif_id)
                
            return bool(matches)

        except (Exception, OSError, BaseException):
            # Resilient to individual corrupted notification packets
            return False

    async def scan_notifications(self):
        """
        Scans current toast notifications from UserNotificationListener.
        """
        try:
            toast_kind = win_notif.NotificationKinds.TOAST
            notifications = await self.listener.get_notifications_async(toast_kind)
            for notif in notifications:
                self.process_single_notification(notif)
        except Exception as e:
            pass

    async def run(self):
        """
        Starts processing:
        1. Optionally scans and evaluates existing notifications on startup.
        2. Attempts to register the native NotificationChanged event.
        3. If running as an unpackaged Python script (which lacks UWP Appx identity
           required by Windows for the event sink callback), falls back to a high-efficiency
           async polling loop without any interruption.
        """
        self.is_running = True
        
        # 1. Process or mark existing notifications
        try:
            toast_kind = win_notif.NotificationKinds.TOAST
            initial_notifs = await self.listener.get_notifications_async(toast_kind)
            
            if PROCESS_EXISTING_ON_STARTUP:
                print(f"{Fore.CYAN}[*] Processing {len(initial_notifs)} existing notification(s)...{Style.RESET_ALL}")
                match_count = 0
                for notif in initial_notifs:
                    if self.process_single_notification(notif):
                        match_count += 1
                print(f"{Fore.GREEN}[+] Startup processing complete ({match_count} rule matches).{Style.RESET_ALL}")
            else:
                for notif in initial_notifs:
                    self.seen_notification_ids.add(notif.id)
                print(f"{Fore.CYAN}[*] Marked {len(initial_notifs)} existing notifications as seen.{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.YELLOW}[!] Note: Could not fetch initial notification snapshot: {e}{Style.RESET_ALL}")

        # 2. Try subscribing to NotificationChanged event
        event_registered = False
        try:
            def on_notification_changed(listener, args):
                # When notification changed event fires, schedule a scan
                asyncio.run_coroutine_threadsafe(self.scan_notifications(), loop)

            loop = asyncio.get_running_loop()
            self.event_token = self.listener.add_notification_changed(on_notification_changed)
            event_registered = True
            print(f"{Fore.GREEN}[+] Subscribed to native NotificationChanged events.{Style.RESET_ALL}")
        except OSError as oe:
            # WinRT NotificationChanged requires UWP/MSIX Package Identity with userNotificationListener capability.
            # Unpackaged Python processes will throw OSError 'Element not found'.
            print(
                f"{Fore.YELLOW}[i] Running as unpackaged desktop Python process: "
                f"Using async listener loop (every {POLL_INTERVAL_SECONDS}s).{Style.RESET_ALL}"
            )
        except Exception as e:
            print(f"{Fore.YELLOW}[i] NotificationChanged event registration: {e}. Using listener loop.{Style.RESET_ALL}")

        print(f"\n{Fore.GREEN}{Style.BRIGHT}>>> WhatsApp Notification Monitor is active. Waiting for messages... <<<{Style.RESET_ALL}")
        print(f"{Fore.WHITE}Press Ctrl+C to stop.\n{Style.RESET_ALL}")

        # 3. Continuous monitoring loop
        try:
            while self.is_running:
                if not event_registered:
                    await self.scan_notifications()
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass
        finally:
            self.stop()

    def stop(self):
        """Clean up event handlers and stop listening."""
        self.is_running = False
        if self.event_token and self.listener:
            try:
                self.listener.remove_notification_changed(self.event_token)
                self.event_token = None
            except Exception:
                pass


# ==============================================================================
#                                ENTRY POINT
# ==============================================================================
async def main_async():
    watcher = WhatsAppNotificationWatcher(RULES)
    success = await watcher.initialize()
    if not success:
        return
        
    if ENABLE_SOUND:
        startup_snd = os.path.join(MEDIA_DIR, "notify.wav")
        play_sound(startup_snd)
        print(f"{Fore.GREEN}[+] Sound system active (tested speaker audio with notify.wav).{Style.RESET_ALL}")
        
    await watcher.run()

def main():
    print(Fore.BLUE + Style.BRIGHT + "=" * 70)
    print(Fore.WHITE + Style.BRIGHT + "       WHATSAPP NOTIFICATION MONITOR & KEYWORD DETECTOR")
    print(Fore.BLUE + Style.BRIGHT + "=" * 70 + Style.RESET_ALL)
    
    # Optional sound preview mode: run 'python whatsapp_detector.py --test-sounds'
    if "--test-sounds" in sys.argv:
        print(f"\n{Fore.CYAN}Playing sound preview for each rule...{Style.RESET_ALL}")
        import time
        for idx, rule in enumerate(RULES, start=1):
            sound_path = get_sound_for_rule(rule, idx - 1)
            sound_name = os.path.basename(sound_path)
            color = get_color_for_rule(rule, idx - 1)
            print(f"  {idx}. {color}[{rule['type']}]{Style.RESET_ALL} -> {sound_name}")
            winsound.PlaySound(sound_path, winsound.SND_FILENAME)
            time.sleep(0.3)
        print(f"\n{Fore.GREEN}[+] Sound preview finished! All sounds working.{Style.RESET_ALL}\n")
        return

    print(f"\n{Fore.CYAN}Loaded Rules ({len(RULES)}):{Style.RESET_ALL}")
    for idx, rule in enumerate(RULES, start=1):
        color = get_color_for_rule(rule, idx - 1)
        sound_path = get_sound_for_rule(rule, idx - 1)
        sound_name = os.path.basename(sound_path)
        print(
            f"  {idx}. {color}[{rule['type']}]{Style.RESET_ALL} "
            f"(mode: '{rule.get('mode', 'any')}') -> {rule.get('keywords', [])} "
            f"{Fore.LIGHTBLACK_EX}[Sound: {sound_name}]{Style.RESET_ALL}"
        )
    print()

    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}[!] Stopping WhatsApp Notification Monitor...{Style.RESET_ALL}")
        print(f"{Fore.GREEN}[+] Exited cleanly. Goodbye!{Style.RESET_ALL}")


if __name__ == "__main__":
    main()


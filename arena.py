
---

## File 4: `arena.py`

```python
#!/usr/bin/env python3
"""
lmarena-cli — Use lmarena.ai from Debian terminal.
Models fetched live from site. Auto-updates.
"""

import sys
import os
import time
import json
import re
import textwrap
import subprocess
import shutil
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Run: ./setup.sh first")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.table import Table
    from rich.syntax import Syntax
    from rich.prompt import Prompt, IntPrompt
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
DEFAULT_CONFIG = {
    "url": "https://lmarena.ai",
    "headless": True,
    "timeout": 120,
    "save_logs": True,
    "log_dir": "logs",
    "screenshot_dir": "screenshots",
    "default_mode": "chat",
    "default_model": None,
    "max_retries": 3,
}


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)


# ─────────────────────────────────────────────
# DISPLAY
# ─────────────────────────────────────────────

if HAS_RICH:
    console = Console()

    def banner():
        console.print(Panel(
            "[bold cyan]lmarena.ai CLI[/bold cyan]\n"
            "[dim]Chat with any AI model from your terminal[/dim]",
            box=box.DOUBLE,
            border_style="cyan",
            padding=(1, 4),
        ))

    def info(msg):
        console.print(f"  [green]✓[/green] {msg}")

    def warn(msg):
        console.print(f"  [yellow]⚠[/yellow] {msg}")

    def error(msg):
        console.print(f"  [red]✗[/red] {msg}")

    def show_reply(model, text):
        console.print()
        # detect code blocks
        if "```" in text:
            console.print(Panel(
                Markdown(text),
                title=f"[bold]{model}[/bold]",
                border_style="blue",
                padding=(1, 2),
            ))
        else:
            console.print(Panel(
                text,
                title=f"[bold]{model}[/bold]",
                border_style="blue",
                padding=(1, 2),
            ))

    def show_table(title, rows):
        table = Table(title=title, box=box.SIMPLE)
        table.add_column("#", style="dim", width=4)
        table.add_column("Name", style="cyan")
        for i, r in enumerate(rows, 1):
            table.add_row(str(i), r)
        console.print(table)

else:
    def banner():
        print("=" * 55)
        print("  lmarena.ai CLI")
        print("  Chat with any AI model from your terminal")
        print("=" * 55)

    def info(msg):
        print(f"  ✓ {msg}")

    def warn(msg):
        print(f"  ⚠ {msg}")

    def error(msg):
        print(f"  ✗ {msg}")

    def show_reply(model, text):
        print(f"\n  {model}:\n")
        for line in text.split("\n"):
            print(textwrap.fill(
                line, 70,
                initial_indent="    ",
                subsequent_indent="    ",
            ))

    def show_table(title, rows):
        print(f"\n  {title}\n")
        for i, r in enumerate(rows, 1):
            print(f"    {i:2d}) {r}")


# ─────────────────────────────────────────────
# BROWSER
# ─────────────────────────────────────────────

class Arena:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pw = None
        self.browser = None
        self.page = None
        self.models = []
        self.current_model = None
        self.mode = cfg.get("default_mode", "chat")
        self.history = []
        self.last_user_msg = None
        self.last_reply = None
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # ensure dirs
        Path(cfg["log_dir"]).mkdir(exist_ok=True)
        Path(cfg["screenshot_dir"]).mkdir(exist_ok=True)

    # ── CONNECT ──

    def connect(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
            headless=self.cfg["headless"]
        )
        self.page = self.browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        ).new_page()

        info("Loading lmarena.ai ...")
        self.page.goto(
            self.cfg["url"],
            timeout=60000,
            wait_until="networkidle",
        )
        time.sleep(4)
        info("Connected")

    def disconnect(self):
        try:
            self.browser.close()
            self.pw.stop()
        except Exception:
            pass

    # ── NAVIGATE ──

    def click_direct_chat(self):
        selectors = [
            'button:has-text("Direct Chat")',
            'button:has-text("direct chat")',
            'button[role="tab"]:has-text("Direct")',
            'a:has-text("Direct Chat")',
        ]
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    time.sleep(2)
                    info("Direct Chat tab opened")
                    return True
            except Exception:
                continue
        warn("Direct Chat tab not found, using default view")
        return False

    # ── MODELS (LIVE FROM SITE) ──

    def fetch_models(self):
        """5 methods to get models from the live site. No hardcoding."""
        page = self.page
        models = []

        # Method 1: Click Gradio dropdown, read items
        if not models:
            models = self._models_from_dropdown(page)

        # Method 2: <select> element
        if not models:
            models = self._models_from_select(page)

        # Method 3: JavaScript — gradio internal config
        if not models:
            models = self._models_from_js_config(page)

        # Method 4: Fetch /config endpoint
        if not models:
            models = self._models_from_config_endpoint(page)

        # Method 5: Regex on page HTML
        if not models:
            models = self._models_from_html_regex(page)

        # Deduplicate
        seen = set()
        unique = []
        for m in models:
            m = m.strip()
            if m and m not in seen and m not in ("---", "", "Select"):
                seen.add(m)
                unique.append(m)

        if not unique:
            error("Could not fetch models from lmarena.ai")
            self.take_screenshot("no-models")
            error(f"Screenshot saved for debugging")
            sys.exit(1)

        self.models = unique
        info(f"Fetched {len(unique)} models from site")
        return unique

    def _models_from_dropdown(self, page):
        models = []
        try:
            triggers = page.locator(
                'input[role="listbox"],'
                'input[aria-haspopup="listbox"],'
                '.wrap-inner .secondary-wrap,'
                'input.border-none,'
                'div[data-testid="dropdown"]'
            ).all()
            for trigger in triggers:
                try:
                    if not trigger.is_visible(timeout=1000):
                        continue
                    trigger.click()
                    time.sleep(1)
                    items = page.locator(
                        'ul[role="listbox"] li,'
                        '.dropdown-item,'
                        'ul.options li,'
                        'div[role="option"]'
                    ).all()
                    for item in items:
                        t = item.text_content().strip()
                        if t:
                            models.append(t)
                    page.keyboard.press("Escape")
                    time.sleep(0.3)
                    if models:
                        break
                except Exception:
                    continue
        except Exception:
            pass
        return models

    def _models_from_select(self, page):
        models = []
        try:
            for sel in page.locator("select").all():
                opts = sel.locator("option").all()
                for o in opts:
                    t = o.text_content().strip()
                    if t:
                        models.append(t)
                if models:
                    break
        except Exception:
            pass
        return models

    def _models_from_js_config(self, page):
        models = []
        try:
            result = page.evaluate("""
                () => {
                    const gc = window.gradio_config
                             || window.__gradio_config__
                             || null;
                    if (!gc || !gc.components) return [];
                    for (const comp of gc.components) {
                        const ch = (comp.props || {}).choices;
                        if (Array.isArray(ch) && ch.length > 5) {
                            return ch.map(c =>
                                Array.isArray(c) ? c[0] : String(c)
                            );
                        }
                    }
                    return [];
                }
            """)
            if result:
                models = [str(m) for m in result if m]
        except Exception:
            pass
        return models

    def _models_from_config_endpoint(self, page):
        models = []
        try:
            config_text = page.evaluate(
                "async () => {"
                "  const r = await fetch('/config');"
                "  return await r.text();"
                "}"
            )
            config = json.loads(config_text)
            for comp in config.get("components", []):
                choices = comp.get("props", {}).get("choices", [])
                if len(choices) > 5:
                    models = [
                        c[0] if isinstance(c, list) else str(c)
                        for c in choices
                    ]
                    break
        except Exception:
            pass
        return models

    def _models_from_html_regex(self, page):
        models = []
        try:
            html = page.content()
            match = re.search(r'"choices"\s*:\s*(\[.*?\])', html)
            if match:
                parsed = json.loads(match.group(1))
                models = [
                    p[0] if isinstance(p, list) else str(p)
                    for p in parsed if p
                ]
        except Exception:
            pass
        return models

    # ── SELECT MODEL ON PAGE ──

    def select_model(self, model):
        page = self.page
        self.current_model = model

        # Gradio dropdown
        try:
            dd = page.locator(
                'input[role="listbox"],'
                'input[aria-haspopup="listbox"],'
                'input.border-none'
            ).first
            dd.click()
            time.sleep(0.5)
            dd.fill("")
            dd.fill(model)
            time.sleep(1)
            item = page.locator(f'li:has-text("{model}")').first
            if item.is_visible(timeout=3000):
                item.click()
                time.sleep(0.5)
                info(f"Model: {model}")
                return True
        except Exception:
            pass

        # <select>
        try:
            page.select_option("select", label=model)
            info(f"Model: {model}")
            return True
        except Exception:
            pass

        warn(f"Could not auto-select {model} in dropdown")
        return False

    # ── SEND MESSAGE ──

    def send(self, msg):
        page = self.page
        retries = self.cfg.get("max_retries", 3)

        for attempt in range(retries):
            result = self._try_send(page, msg)
            if result:
                return result
            if attempt < retries - 1:
                warn(f"Retry {attempt + 2}/{retries}...")
                time.sleep(2)

        error("No response after retries")
        self.take_screenshot("no-response")
        return None

    def _try_send(self, page, msg):
        # ── Find and fill textbox ──
        typed = False
        for sel in [
            "textarea",
            "textarea[placeholder]",
            ".chat-input textarea",
            'div[data-testid="textbox"] textarea',
        ]:
            try:
                tb = page.locator(sel).first
                if tb.is_visible(timeout=2000):
                    tb.fill(msg)
                    time.sleep(0.3)
                    typed = True
                    break
            except Exception:
                continue

        if not typed:
            error("No textbox found")
            return None

        # ── Click send ──
        sent = False
        for sel in [
            'button:has-text("Send")',
            'button:has-text("send")',
            "button.primary",
            'button[aria-label="Send"]',
            'button.submit',
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    sent = True
                    break
            except Exception:
                continue

        if not sent:
            page.keyboard.press("Enter")

        # ── Wait for response ──
        time.sleep(2)
        timeout_secs = self.cfg.get("timeout", 120)
        for _ in range(timeout_secs):
            try:
                loading = page.locator(
                    ".generating, .loading, .pending"
                )
                if (loading.count() > 0
                        and loading.first.is_visible(timeout=500)):
                    time.sleep(1)
                    continue
            except Exception:
                pass
            break
        time.sleep(1)

        # ── Extract response ──
        return self._extract_response(page)

    def _extract_response(self, page):
        # Try specific selectors
        for sel in [
            ".message.bot:last-child",
            ".bot:last-child",
            "div[data-testid='bot']:last-child",
            ".chatbot .message:last-child",
            ".message-wrap .bot:last-child",
        ]:
            try:
                els = page.locator(sel).all()
                if els:
                    txt = els[-1].text_content().strip()
                    if txt:
                        return txt
            except Exception:
                continue

        # Fallback: entire chatbot container, grab last chunk
        try:
            cb = page.locator(
                ".chatbot, #chatbot, .chat-container"
            ).first
            full = cb.inner_text().strip()
            if full:
                # Split by common separators and take last block
                parts = re.split(r'\n{2,}', full)
                if parts:
                    return parts[-1].strip()
        except Exception:
            pass

        return None

    # ── UTILITIES ──

    def take_screenshot(self, label="debug"):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path(self.cfg["screenshot_dir"]) / f"{label}_{ts}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
            info(f"Screenshot: {path}")
        except Exception as e:
            error(f"Screenshot failed: {e}")
        return path

    def save_log(self):
        if not self.cfg.get("save_logs") or not self.history:
            return
        path = Path(self.cfg["log_dir"]) / f"chat_{self.session_id}.json"
        data = {
            "session": self.session_id,
            "model": self.current_model,
            "mode": self.mode,
            "timestamp": datetime.now().isoformat(),
            "messages": self.history,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        info(f"Log saved: {path}")

    def export_markdown(self):
        if not self.history:
            warn("No conversation to export")
            return
        path = (
            Path(self.cfg["log_dir"])
            / f"chat_{self.session_id}.md"
        )
        lines = [
            f"# Chat with {self.current_model}",
            f"*Mode: {self.mode} | "
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
            "",
            "---",
            "",
        ]
        for msg in self.history:
            if msg["role"] == "user":
                lines.append(f"**You:** {msg['content']}")
            else:
                lines.append(f"**{self.current_model}:**\n")
                lines.append(msg["content"])
            lines.append("")
        with open(path, "w") as f:
            f.write("\n".join(lines))
        info(f"Exported: {path}")

    def copy_to_clipboard(self, text):
        try:
            proc = subprocess.Popen(
                ["xclip", "-selection", "clipboard"],
                stdin=subprocess.PIPE,
            )
            proc.communicate(text.encode())
            info("Copied to clipboard")
        except FileNotFoundError:
            # try xsel
            try:
                proc = subprocess.Popen(
                    ["xsel", "--clipboard", "--input"],
                    stdin=subprocess.PIPE,
                )
                proc.communicate(text.encode())
                info("Copied to clipboard")
            except FileNotFoundError:
                warn("Install xclip: sudo apt install xclip")

    def clear_chat(self):
        """Click new/clear chat button on the page."""
        for sel in [
            'button:has-text("Clear")',
            'button:has-text("New Chat")',
            'button:has-text("clear")',
            'button:has-text("🗑")',
            'button[aria-label="Clear"]',
        ]:
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    time.sleep(1)
                    self.history = []
                    info("Conversation cleared")
                    return
            except Exception:
                continue
        # fallback: reload page
        warn("No clear button found, reloading page...")
        self.page.reload(wait_until="networkidle")
        time.sleep(3)
        self.click_direct_chat()
        self.select_model(self.current_model)
        self.history = []
        info("Page reloaded, conversation cleared")


# ─────────────────────────────────────────────
# HELP
# ─────────────────────────────────────────────

HELP_TEXT = """
  /help        — Show this help
  /models      — List models & switch
  /mode        — Switch mode (chat/code/search)
  /clear       — Clear conversation
  /retry       — Retry last message
  /save        — Save conversation (JSON)
  /export      — Export as Markdown
  /copy        — Copy last reply to clipboard
  /history     — Show conversation history
  /screenshot  — Take page screenshot
  /debug       — Toggle headless/visible browser
  /config      — Show current config
  /quit        — Exit
"""

MODES = {
    "1": ("chat",   "Normal Chat",  ""),
    "2": ("code",   "Code",         "Write code: "),
    "3": ("search", "Search",       "Search and answer: "),
}


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def pick_mode():
    print("\n  Select mode:\n")
    for k, (_, label, _) in MODES.items():
        print(f"    {k}) {label}")
    while True:
        c = input("\n  Mode [1/2/3]: ").strip()
        if c in MODES:
            return MODES[c]
        print("  Invalid choice")


def pick_model(models, default=None):
    show_table("Models (live from site)", models)
    while True:
        prompt = f"\n  Pick [1-{len(models)}]"
        if default:
            prompt += f" (default: {default})"
        prompt += ": "
        c = input(prompt).strip()
        if not c and default and default in models:
            return default
        try:
            return models[int(c) - 1]
        except (ValueError, IndexError):
            print("  Invalid")


def main():
    cfg = load_config()
    arena = Arena(cfg)

    banner()

    # ── Step 1: Mode ──
    mode_id, mode_label, prefix = pick_mode()
    arena.mode = mode_id
    info(f"Mode: {mode_label}")

    # ── Step 2: Connect ──
    arena.connect()
    arena.click_direct_chat()

    # ── Step 3: Models (live) ──
    models = arena.fetch_models()

    # ── Step 4: Pick model ──
    default = cfg.get("default_model")
    model = pick_model(models, default)
    arena.select_model(model)

    # ── Ready ──
    print()
    if HAS_RICH:
        console.rule("[bold green]Ready[/bold green]")
        console.print(
            "  Type a message or [cyan]/help[/cyan] for commands\n"
        )
    else:
        print("─" * 55)
        print("  Ready. Type /help for commands.")
        print("─" * 55)

    # ── Chat loop ──
    while True:
        try:
            user = input("\n  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user:
            continue

        # ── COMMANDS ──

        if user.startswith("/"):
            cmd = user.lower().split()[0]

            if cmd == "/quit" or cmd == "/exit" or cmd == "/q":
                break

            elif cmd == "/help":
                print(HELP_TEXT)

            elif cmd == "/models":
                info("Refreshing models from site...")
                arena.fetch_models()
                new_model = pick_model(arena.models)
                arena.select_model(new_model)

            elif cmd == "/mode":
                mode_id, mode_label, prefix = pick_mode()
                arena.mode = mode_id
                info(f"Mode: {mode_label}")

            elif cmd == "/clear":
                arena.clear_chat()

            elif cmd == "/retry":
                if arena.last_user_msg:
                    info(f"Retrying: {arena.last_user_msg[:50]}...")
                    msg = prefix + arena.last_user_msg
                    reply = arena.send(msg)
                    if reply:
                        arena.last_reply = reply
                        arena.history.append({
                            "role": "user",
                            "content": arena.last_user_msg,
                        })
                        arena.history.append({
                            "role": "assistant",
                            "content": reply,
                        })
                        show_reply(arena.current_model, reply)
                    else:
                        error("No response")
                else:
                    warn("No previous message to retry")

            elif cmd == "/save":
                arena.save_log()

            elif cmd == "/export":
                arena.export_markdown()

            elif cmd == "/copy":
                if arena.last_reply:
                    arena.copy_to_clipboard(arena.last_reply)
                else:
                    warn("No reply to copy")

            elif cmd == "/history":
                if not arena.history:
                    warn("No history yet")
                else:
                    print()
                    for msg in arena.history:
                        role = msg["role"]
                        text = msg["content"]
                        if role == "user":
                            print(f"  You: {text}")
                        else:
                            short = (
                                text[:80] + "..."
                                if len(text) > 80
                                else text
                            )
                            print(
                                f"  {arena.current_model}: {short}"
                            )
                    print(
                        f"\n  ({len(arena.history)} messages)"
                    )

            elif cmd == "/screenshot":
                arena.take_screenshot("manual")

            elif cmd == "/debug":
                cfg["headless"] = not cfg["headless"]
                save_config(cfg)
                state = (
                    "headless" if cfg["headless"]
                    else "visible"
                )
                info(
                    f"Browser mode: {state} "
                    f"(restart to apply)"
                )

            elif cmd == "/config":
                print(f"\n  {json.dumps(cfg, indent=4)}\n")

            else:
                warn(f"Unknown command: {cmd}")
                print("  Type /help for commands")

            continue

        # ── SEND MESSAGE ──

        arena.last_user_msg = user
        msg = prefix + user

        if HAS_RICH:
            with console.status(
                f"[cyan]{arena.current_model} thinking...[/cyan]"
            ):
                reply = arena.send(msg)
        else:
            print(
                f"\n  {arena.current_model}: thinking...",
                end="\r",
                flush=True,
            )
            reply = arena.send(msg)
            print(" " * 60, end="\r")

        if reply:
            arena.last_reply = reply
            arena.history.append({
                "role": "user",
                "content": user,
            })
            arena.history.append({
                "role": "assistant",
                "content": reply,
            })
            show_reply(arena.current_model, reply)
        else:
            error("No response received")
            warn("Try /retry or /screenshot to debug")

    # ── EXIT ──
    if arena.history:
        arena.save_log()
    arena.disconnect()

    if HAS_RICH:
        console.print("\n  [bold]Bye![/bold]\n")
    else:
        print("\n  Bye!\n")


if __name__ == "__main__":
    main()

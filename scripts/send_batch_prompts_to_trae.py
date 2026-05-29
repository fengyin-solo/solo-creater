#!/usr/bin/env python3
"""Open Trae projects and submit generated solo-create prompts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from batch_prompt_workbook import pick, update


SKILL_DIR = Path(__file__).resolve().parents[1]
ENSURE_TRAE = SKILL_DIR / "scripts" / "ensure_trae_project_open.py"
TRAE_APP = "Trae CN"
TRAE_PROCESS = "TRAE CN"


def run_command(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, input=input_text, capture_output=True, text=True)


def copy_to_clipboard(text: str) -> None:
    result = run_command("pbcopy", input_text=text)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "复制提示词失败")


def focus_project_window(project_name: str) -> None:
    script = f'''
on run argv
  set projectName to item 1 of argv
  tell application "{TRAE_APP}" to activate
  delay 0.8
  tell application "System Events"
    tell process "{TRAE_PROCESS}"
      set frontmost to true
      repeat with candidateWindow in windows
        if (name of candidateWindow contains projectName) then
          perform action "AXRaise" of candidateWindow
          delay 0.4
          return name of candidateWindow
        end if
      end repeat
      if (count of windows) > 0 then
        return "front-window:" & name of window 1
      end if
    end tell
  end tell
  return "no-window"
end run
'''
    result = run_command("osascript", "-e", script, project_name)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "切换 Trae 项目窗口失败")
    focused_title = result.stdout.strip()
    if project_name not in focused_title:
        raise RuntimeError(f"未聚焦到目标 Trae 窗口：{focused_title or '无窗口'}")


def submit_clipboard_to_trae(project_name: str, before_enter_delay: float, after_enter_delay: float) -> None:
    script = f'''
on run argv
  set projectName to item 1 of argv
  set beforeEnterDelay to item 2 of argv as real
  tell application "{TRAE_APP}" to activate
  delay 0.4
  tell application "System Events"
    tell process "{TRAE_PROCESS}"
      set frontmost to true
      if (count of windows) = 0 then error "Trae 没有可用窗口"
      if (name of window 1 does not contain projectName) then error "前台窗口不是目标项目：" & name of window 1
      keystroke "v" using command down
      delay beforeEnterDelay
      key code 36
    end tell
  end tell
  delay {after_enter_delay}
end run
'''
    result = run_command("osascript", "-e", script, project_name, str(before_enter_delay))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "发送提示词失败")


def open_project(project_path: Path) -> None:
    result = run_command(sys.executable, str(ENSURE_TRAE), "--project-path", str(project_path))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "打开 Trae 项目失败")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", required=True)
    parser.add_argument("--workbook")
    parser.add_argument("--range", help="按 Excel 编号选择，例如 1-6、22-27")
    parser.add_argument("--limit", type=int, default=6, help="不指定范围时默认发送前 N 个")
    parser.add_argument("--open-delay", type=float, default=2.5, help="打开或切换项目后等待秒数")
    parser.add_argument("--before-enter-delay", type=float, default=0.8, help="粘贴提示词后按回车前等待秒数")
    parser.add_argument("--after-enter-delay", type=float, default=2.5, help="按回车发送后等待秒数")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    parent = Path(args.parent).expanduser().resolve()
    selected = pick(parent, args.workbook, args.range, None if args.range else args.limit)
    sent: list[str] = []
    failed: list[dict[str, str]] = []

    for item in selected["items"]:
        folder = item["子文件夹名称"]
        prompt = item.get("提示词", "").strip()
        if not prompt:
            continue
        project_path = parent / folder
        try:
            if not args.dry_run:
                open_project(project_path)
                time.sleep(args.open_delay)
                focus_project_window(project_path.name)
                copy_to_clipboard(prompt)
                submit_clipboard_to_trae(project_path.name, args.before_enter_delay, args.after_enter_delay)
                update(parent, args.workbook, folder, None, "已自动发送到 Trae", "已发送")
            sent.append(folder)
        except Exception as exc:  # noqa: BLE001
            failed.append({"folder": folder, "error": str(exc)})

    print(
        json.dumps(
            {
                "selected_count": selected["count"],
                "sent_count": len(sent),
                "sent": sent,
                "failed": failed,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

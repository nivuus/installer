"""Excerpts of the Debian files common/regional.py reads, for the suites."""
import pathlib

SUPPORTED = """\
ar_SA.UTF-8 UTF-8
ar_SA ISO-8859-6
en_US.UTF-8 UTF-8
en_US ISO-8859-1
fr_FR.UTF-8 UTF-8
ja_JP.UTF-8 UTF-8
ja_JP.EUC-JP EUC-JP
sr_RS@latin UTF-8
zh_CN.UTF-8 UTF-8
"""

BASE_LST = """\
! model
  pc105           Generic 105-key PC

! layout
  us              English (US)
  fr              French
  jp              Japanese
  ara             Arabic

! variant
  intl            us: English (US, intl., with dead keys)
  bepo            fr: French (BEPO)
  kana            jp: Japanese (Kana)

! option
  grp             Switching to another layout
"""


def seed(root, zones=("Etc/UTC", "Asia/Tokyo")):
    """Lay the three sources under `root` (a pathlib.Path)."""
    root = pathlib.Path(root)
    (root / "usr/share/i18n").mkdir(parents=True, exist_ok=True)
    (root / "usr/share/i18n/SUPPORTED").write_text(SUPPORTED)
    (root / "usr/share/X11/xkb/rules").mkdir(parents=True, exist_ok=True)
    (root / "usr/share/X11/xkb/rules/base.lst").write_text(BASE_LST)
    for zone in zones:
        path = root / "usr/share/zoneinfo" / zone
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"TZif")

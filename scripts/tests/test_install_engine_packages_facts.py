#!/usr/bin/env python3
"""Tests for the resolve -> activate facts channel through the engine.

resolve measures what the install itself destroys; the engine persists it;
activate finds it again at first boot. Each of the three links has its own
NAMED assertion, so cutting one says which.

Run: python3 scripts/tests/test_install_engine_packages_facts.py
"""
import json
import os
import stat
import tempfile
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from engine_packages_harness import (  # noqa: E402
    HW, FakeEmit, check, fake_chroot_run, report, seed_payload,
    steps_packages)

# --- le canal resolve -> activate, ses trois maillons ---------------------- #
# resolve mesure ce que l'installation elle-meme detruit (un disque sur le
# point d'etre lie a vfio-pci) ; le moteur le persiste ; activate le retrouve.
# Chaque maillon a son assertion NOMMEE : couper l'un des trois doit dire
# lequel, sinon celui qui ajoutera un quatrieme maillon dans six mois lira
# "le fait a disparu" sans savoir ou.
with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = steps_packages.apt.chroot_run = fake_chroot_run
    target = pathlib.Path(tmp)
    nivuus_dir = seed_payload(target)
    mesure_config = {"disk": {"path": "/dev/nvme0n1"}, "features": [],
                     "packages": {"mesureur": {}}}
    plan, _ = steps_packages.plan_packages(mesure_config, HW, FakeEmit())
    check("MAILLON 1 (packages/runner.py) : le fait mesure par resolve arrive "
          "dans le plan — sans lui rien ne quitte la phase resolve",
          plan[0][2].facts.get("pre_reboot_measure"), 4242)

    steps_packages.apply_packages(plan, tmp, nivuus_dir, HW, FakeEmit())

    state = json.loads((target / "etc/nivuus/packages.json").read_text())
    check("MAILLON 2 (install-engine/steps/packages.py) : le fait est persiste "
          "dans etc/nivuus/packages.json — sans lui il meurt au redemarrage",
          (state["mesureur"].get("facts") or {}).get("pre_reboot_measure"),
          4242)
    # Le fichier porte deja des secrets (mots de passe, clef produit) : les
    # faits le rejoignent, le mode ne s'elargit pas.
    mode = stat.S_IMODE((target / "etc/nivuus/packages.json").stat().st_mode)
    check("l'etat reste en 0600 une fois les faits ajoutes", oct(mode),
          oct(0o600))

    # Maillon 3 : activate_cli.py, l'entree du premier boot. Le hook activate
    # de la fixture ecrit le hw qu'il a VRAIMENT recu par le tube - on
    # n'assertionne pas sur ce que l'appelant croit avoir passe.
    from packages import activate_cli  # noqa: E402

    class FakeHardware:
        """Toute l'interface que activate_cli lit de `hardware` : detect_all.

        `total_cpus` est la POUR ENTRER EN COLLISION avec le fait du meme nom
        emis par la fixture : c'est la regle de precedence de packages/facts.py
        qui se joue ici - la detection fraiche gagne, un fait ne comble que ce
        qu'elle ne produit pas.
        """

        @staticmethod
        def detect_all():
            return {"total_cpus": 24, "memory_mib": 65536}

    hw_out = target / "hw-recu-par-activate.json"
    os.environ["MESUREUR_HW_OUT"] = str(hw_out)
    activate_cli.STATE_FILE = str(target / "etc/nivuus/packages.json")
    activate_cli.STAMP_DIR = str(target / "var/lib/nivuus/packages")
    activate_cli.hardware = FakeHardware
    rc = activate_cli.main(["activate_cli.py", "mesureur"])
    check("l'activation du package aboutit", rc, 0)
    check("le tampon d'activation n'est ecrit qu'au succes",
          (target / "var/lib/nivuus/packages/mesureur.activated").is_file(),
          True)

    received = json.loads(hw_out.read_text())
    check("MAILLON 3 (packages/activate_cli.py) : le hook activate recoit le "
          "fait dans son hw — sans lui le fait dort dans un fichier que "
          "personne ne lit",
          received.get("pre_reboot_measure"), 4242)
    check("PRECEDENCE : la detection fraiche gagne sur un fait de meme clef — "
          "un fait decrit le monde d'AVANT le redemarrage",
          received.get("total_cpus"), 24)
    check("la detection fraiche est passee entiere, pas remplacee",
          received.get("memory_mib"), 65536)

    # A package whose last update failed is not replayed at boot: that would
    # be the automatic retry the updater refuses to make.
    state_path = target / "etc/nivuus/packages.json"
    recorded = json.loads(state_path.read_text())
    recorded["mesureur"].update(state="failed", target_version="9.0.0",
                                error="hook exited 1")
    state_path.write_text(json.dumps(recorded))
    hw_out.unlink()
    check("activate_cli refuses a package whose update failed",
          activate_cli.main(["activate_cli.py", "mesureur"]), 1)
    check("and runs none of its hooks", hw_out.exists(), False)
    del os.environ["MESUREUR_HW_OUT"]

report("OK - all package facts-channel tests passed")

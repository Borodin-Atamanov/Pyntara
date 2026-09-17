"""Values of the imagemagick_setup task.

ImageMagick comes from the Ubuntu archive (the meta package imagemagick
pulls ImageMagick 7 on Kubuntu 26.04 and newer), so apt is the whole install
path. The task then writes the tuned security policy from the template
task_data/imagemagick_setup/POLICY_TEMPLATE_FILE_NAME over POLICY_PATH and
saves the package original once next to it under POLICY_BACKUP_FILE_SUFFIX:
ImageMagick loads only the file named policy.xml, so the backup is never
picked up.
"""

from __future__ import annotations

from pathlib import Path

# The apt packages the task installs before it deploys the policy.
PACKAGES: tuple[str, ...] = ("imagemagick",)

# System ImageMagick security policy the tuned policy is written over.
POLICY_PATH: Path = Path("/etc/ImageMagick-7/policy.xml")

# Name of the tuned policy template in task_data/<task>/ of the clone.
POLICY_TEMPLATE_FILE_NAME: str = "policy.xml"

# Suffix of the backup written next to the policy once. An empty suffix
# would name the policy file itself, so the backup would overwrite the file
# it is meant to preserve.
POLICY_BACKUP_FILE_SUFFIX: str = ".bak"

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not
# declare, instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGES",
    "POLICY_PATH",
    "POLICY_TEMPLATE_FILE_NAME",
    "POLICY_BACKUP_FILE_SUFFIX",
)

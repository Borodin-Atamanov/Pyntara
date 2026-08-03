# Applications, GUI, and workspace

## ImageMagick, FFmpeg, scrcpy

Dedicated tasks:
install latest ImageMagick (possibly from source)
install latest FFmpeg (possibly from source)
install latest scrcpy with all capabilities enabled

For ImageMagick and FFmpeg, provide practical local-machine settings:
rationally high resource limits
prioritize execution stability (hard swap is better than OOM crash)
widest possible format support

## Kate editor

Setting: open a new document by default instead of startup screen.

## Terminal

Settings:
start path: /home/i/Downloads
larger font size
large scrollback history

## Language indicator

Show country flag instead of text.
Use Argentina flag for Spanish.

## User folders

Default folder: /home/i/Downloads.
Folders such as Home i Desktop, Home i Documents, Home i Images, and other unnecessary ones should point to /home/i/Downloads (symlink/hardlink is not critical).
Separate task to remove these extra folders/links from Dolphin sidebar, leaving only /home/i/Downloads.

## Browser workflows

Firefox/Chrome/Chromium:
launch with separate profiles
generate a dedicated JSON
use enterprise policy mechanisms to install required extensions and migrate extension/browser settings
launch browsers in a mode suitable for managing AI agents in a visible user window
goal: transparent cookie transfer from user browsers to AI-managed browsers

## NextDNS

Dedicated task: per-user NextDNS account setup.
Account is created through browser automation.
Unique DNS endpoint is obtained.
This endpoint is applied system-wide so DNS requests go through these DNS servers.
Generated endpoint is included in telemetry.
NextDNS keeps query logs and supports filtering.

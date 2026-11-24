# Installing PhysiqueLapse

PhysiqueLapse is a normal desktop app. You don't need Python, Node or anything else: download one file, install, open.

- [Windows](#windows)
- [Mac](#mac)
- [First launch (both)](#first-launch-both)
- [Updating](#updating)
- [Uninstalling](#uninstalling)
- [Problems](#problems)

All downloads are on the **[Releases page](../../releases/latest)**, under **Assets** at the bottom of the newest release.

| You have | Download |
|---|---|
| Windows 10 or 11 (64-bit) | `PhysiqueLapse-<version>-Setup.exe` |
| Windows, but you can't or don't want to install things | `PhysiqueLapse-<version>-portable.zip` |
| Mac with Apple Silicon (M1, M2, M3, M4 or newer), macOS 12 or later | `PhysiqueLapse-<version>-mac.dmg` |
| Mac with an Intel chip, macOS 12 or later | `PhysiqueLapse-<version>-mac-intel.dmg` |

Not sure which Mac you have? Apple menu (top-left) → **About This Mac**. "Chip: Apple M…" means Apple Silicon (`-mac.dmg`); "Processor: Intel…" means Intel (`-mac-intel.dmg`). An Intel Mac can't open the Apple Silicon version; an Apple Silicon Mac can run the Intel one, but slower, so pick the right one.

---

## Windows

1. Download **`PhysiqueLapse-<version>-Setup.exe`**.
2. Double-click it.
3. Windows will probably show a blue **"Windows protected your PC"** box. That's because the app isn't paid-signed, not because anything is wrong. Click **More info**, then **Run anyway**.
4. Click through the installer. It installs only for you, so it doesn't ask for an administrator password. Leave **Create a desktop shortcut** ticked if you want one.
5. Open **PhysiqueLapse** from the Start menu or the desktop shortcut.

The app opens in its own window. That's it.

### Windows without installing (portable)

1. Download **`PhysiqueLapse-<version>-portable.zip`**.
2. Right-click it → **Extract All…** and pick a folder (for example your Documents or a USB stick).
3. Open the extracted **PhysiqueLapse** folder and double-click **`PhysiqueLapse.exe`**.
4. If the blue "Windows protected your PC" box appears: **More info** → **Run anyway**.

Keep the whole folder together. `PhysiqueLapse.exe` doesn't work on its own.

---

## Mac

1. Download **`PhysiqueLapse-<version>-mac.dmg`** (Apple Silicon) or **`PhysiqueLapse-<version>-mac-intel.dmg`** (Intel).
2. Double-click the `.dmg`. A window opens with the PhysiqueLapse icon and an Applications folder.
3. **Drag PhysiqueLapse onto Applications.**
4. Eject the disk image (the ⏏ next to "PhysiqueLapse" in Finder's sidebar). You can delete the `.dmg` afterwards.
5. Open **Applications** and double-click **PhysiqueLapse**.

### "PhysiqueLapse can't be opened" / "Apple could not verify…"

The first time, macOS blocks the app. That's because it isn't notarized by Apple (which costs a paid developer account), not because anything is wrong. You only have to allow it once:

1. Click **Done** (or **OK**) on the warning. Don't click "Move to Trash".
2. Open the Apple menu → **System Settings** → **Privacy & Security**.
3. Scroll down to the **Security** section. You'll see *"PhysiqueLapse" was blocked to protect your Mac*. Click **Open Anyway**.
4. Enter your Mac password or use Touch ID, then click **Open Anyway** again.

From then on it opens normally.

**Or, with Terminal** (one line, same effect): open **Terminal** (Applications → Utilities) and paste:

```bash
xattr -dr com.apple.quarantine /Applications/PhysiqueLapse.app
```

Press Enter, then open the app normally.

### Intel Mac

Same steps as above, with **`PhysiqueLapse-<version>-mac-intel.dmg`**. It runs the same app. The image model runs on the CPU (on Intel Macs Apple's Core ML was slower than the CPU in our tests), so sorting thousands of photos takes longer than on an Apple Silicon Mac.

---

## First launch (both)

- The first start takes a little longer: the app downloads its pose and face models (about **75 MB, one time**). Progress shows in the job panel at the bottom right. After that it works fully offline.
- A short **tutorial** walks you through the app. You can replay it any time from **Start here**.
- Then: **Import** your photos. Once they're in, the app finds the ones that show your physique and asks you to pick a few of your own photos for each pose (skip any pose you don't do). Then it sorts and lines everything up.
- **GPU:** the app uses your graphics chip automatically (Windows: NVIDIA, AMD or Intel; Mac: the Apple GPU / Neural Engine) and falls back to the CPU when it can't. **Settings → System** shows what it's using.

### Where your photos are kept

Your photos never leave your computer. The app copies them into its own library folder and never changes your originals.

| | Library folder |
|---|---|
| Windows | `%LOCALAPPDATA%\PhysiqueLapse` (paste that into the File Explorer address bar) |
| Mac | `~/Library/Application Support/PhysiqueLapse` (Finder → **Go** → **Go to Folder…**, paste it) |

**Backup:** close the app and copy that folder somewhere safe.

---

## Updating

- **Windows:** download the new `Setup.exe` and run it. It installs over the old version. Your library is kept.
- **Windows portable:** extract the new zip and use the new folder. Your library is kept (it isn't inside the app folder).
- **Mac:** download the new `.dmg`, drag PhysiqueLapse onto Applications and choose **Replace**. Your library is kept. You may have to do the **Open Anyway** step once more.

## Uninstalling

- **Windows:** Settings → Apps → Installed apps → **PhysiqueLapse** → Uninstall. It asks whether to delete your library too. Say **No** to keep your photos for a later reinstall.
- **Windows portable:** delete the folder. To also remove your library, delete `%LOCALAPPDATA%\PhysiqueLapse`.
- **Mac:** drag **PhysiqueLapse** from Applications to the Trash. Your library stays in `~/Library/Application Support/PhysiqueLapse`; delete that folder too if you want everything gone.

---

## Problems

| What you see | What to do |
|---|---|
| Windows: "Windows protected your PC" | **More info → Run anyway** (see [Windows](#windows)) |
| Mac: "can't be opened", "Apple could not verify", or "is damaged and can't be opened" | Do the **Open Anyway** steps or the Terminal line in [Mac](#mac). "Damaged" is the same block, not a broken download. |
| Mac: "You can't open the application… because this application is not supported on this Mac" | You have an Intel Mac and downloaded the Apple Silicon version. Get `-mac-intel.dmg` instead. |
| Mac: nothing happens when you click Open Anyway | Open the app from Applications once more right after clicking it; the button only works for a short while after the blocked attempt. |
| The window stays on "Starting…" | The first launch can take a minute. If it never gets past it, look at the log: `logs/physiquelapse.log` inside the library folder. |
| "Downloading models" fails | The app needs internet once for the ~75 MB model download. Check your connection or firewall, then restart the app. |
| Settings → System says the image model runs on the CPU | Fine, just slower. On Windows, updating your graphics driver usually lets it use the GPU. |
| Something else | Open an issue on GitHub and attach `logs/physiquelapse.log` from the library folder. |

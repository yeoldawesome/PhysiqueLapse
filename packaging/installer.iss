; Inno Setup script for PhysiqueLapse (built by packaging\build.ps1 or the GitHub release workflow).
; Per-user install: no administrator rights needed. The user's photos and library live in
; %LOCALAPPDATA%\PhysiqueLapse and are kept on uninstall unless the user chooses otherwise.

#ifndef AppVersion
  #define AppVersion "0.1.1"
#endif

[Setup]
AppId={{7C1B5E0A-3F5D-4C55-9B8E-5A2E7B1F4D21}
AppName=PhysiqueLapse
AppVersion={#AppVersion}
AppVerName=PhysiqueLapse {#AppVersion}
AppPublisher=PhysiqueLapse
DefaultDirName={localappdata}\Programs\PhysiqueLapse
DefaultGroupName=PhysiqueLapse
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=PhysiqueLapse-{#AppVersion}-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\PhysiqueLapse.exe
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
MinVersion=10.0

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\PhysiqueLapse\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PhysiqueLapse"; Filename: "{app}\PhysiqueLapse.exe"
Name: "{autodesktop}\PhysiqueLapse"; Filename: "{app}\PhysiqueLapse.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PhysiqueLapse.exe"; Description: "Start PhysiqueLapse"; Flags: nowait postinstall skipifsilent

[Code]
// On uninstall, ask whether to delete the photo library too (default: keep it).
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\PhysiqueLapse');
    if DirExists(DataDir) then
      if MsgBox('Also delete your PhysiqueLapse library (photos, settings and downloaded models) in' + #13#10 + DataDir + '?' + #13#10#13#10 +
                'Choose No to keep it for a later reinstall.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;

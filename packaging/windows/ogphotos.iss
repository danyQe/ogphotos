; OGphotos Windows installer (Inno Setup 6). Built by build.ps1, which passes:
;   /DAppVersion=0.1.0  /DStageDir=<folder that contains ogphotos\>  /DOutDir=<output folder>
; Needs Inno Setup 6.3 or newer. x64os only: Windows on ARM is refused (no ARM64 environment
; in the lockfile).
; The install is per-user (no admin). The Python environment is built on first launch under
; %LOCALAPPDATA%\OGphotos, outside the install folder, so upgrades keep it.

#ifndef AppVersion
  #error Pass /DAppVersion=X.Y.Z
#endif
#ifndef StageDir
  #error Pass /DStageDir=<folder containing ogphotos>
#endif
#ifndef OutDir
  #define OutDir "..\..\dist"
#endif

[Setup]
; Fixed forever: Windows uses it to recognise upgrades and the uninstall entry.
AppId={{0CE68C7B-1102-4C29-94EB-F30C0638CC4D}
AppName=OGphotos
AppVersion={#AppVersion}
AppPublisher=OGphotos
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\OGphotos
DefaultGroupName=OGphotos
DisableProgramGroupPage=yes
OutputDir={#OutDir}
OutputBaseFilename=OGphotos-Setup-{#AppVersion}
SetupIconFile={#StageDir}\ogphotos\icons\ogphotos.ico
UninstallDisplayIcon={app}\icons\ogphotos.ico
LicenseFile={#StageDir}\ogphotos\LICENSE
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "{#StageDir}\ogphotos\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userdesktop}\OGphotos"; Filename: "{app}\launch.cmd"; IconFilename: "{app}\icons\ogphotos.ico"
Name: "{userprograms}\OGphotos"; Filename: "{app}\launch.cmd"; IconFilename: "{app}\icons\ogphotos.ico"

[UninstallDelete]
; Python may write __pycache__ under app\ at run time; Inno only removes files it installed.
Type: filesandordirs; Name: "{app}\app"

[Run]
Filename: "{app}\launch.cmd"; Description: "Launch OGphotos"; Flags: postinstall nowait shellexec skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Base: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Never prompts (and never deletes) in a silent uninstall; otherwise the default is No.
    // Only the three environment folders are removed: never the data folder, photos, library or models.
    if (not UninstallSilent) and (MsgBox('Also remove OGphotos'' downloaded Python environment (about 1 GB)? ' +
        'Your photos, library and models are not touched.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
    begin
      Base := ExpandConstant('{localappdata}\OGphotos');
      DelTree(Base + '\python', True, True, True);
      DelTree(Base + '\env', True, True, True);
      DelTree(Base + '\cache', True, True, True);
    end;
  end;
end;

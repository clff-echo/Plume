#ifndef AppVersion
#define AppVersion "0.6.0"
#endif
[Setup]
AppId=io.plume.Client
AppName=Plume
AppVersion={#AppVersion}
AppPublisher=clff-echo
AppPublisherURL=https://github.com/clff-echo/Plume
DefaultDirName={localappdata}\Programs\Plume
DefaultGroupName=Plume
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Plume-{#AppVersion}-windows-x64
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\Plume.exe
[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[Files]
Source: "..\dist\Plume\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Plume"; Filename: "{app}\Plume.exe"
Name: "{autodesktop}\Plume"; Filename: "{app}\Plume.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le bureau"; Flags: unchecked
[Run]
Filename: "{app}\Plume.exe"; Description: "Ouvrir Plume"; Flags: nowait postinstall skipifsilent

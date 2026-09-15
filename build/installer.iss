; Smart File Organizer 4.0 -- Inno Setup wizard installer
; Build the exe first (see SmartFileOrganizer.spec), then compile this with
; Inno Setup 6:  iscc build\installer.iss
; Produces build\output\SmartFileOrganizerSetup.exe -- a normal
; Next > Next > Install > Finish Windows installer you can share.

#define MyAppName "Smart File Organizer"
#define MyAppVersion "4.0"
#define MyAppPublisher "Smart File Organizer"
#define MyAppExeName "SmartFileOrganizer.exe"

[Setup]
AppId={{B7B9B6B0-6C1A-4E9A-9C3D-5F8B2A1E7D41}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Smart File Organizer
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
DisableProgramGroupPage=yes
OutputDir=.\output
OutputBaseFilename=SmartFileOrganizerSetup
SetupIconFile=..\app\organizer.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startuponlogon"; Description: "&Start Smart File Organizer when I log in"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "dist\SmartFileOrganizer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\app\rules.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startuponlogon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Leaves %APPDATA%\SmartFileOrganizer (settings, rules, journal, search index,
; usage ledger, pins, AI-call log) untouched on uninstall — your organized
; files and Undo history survive a reinstall.

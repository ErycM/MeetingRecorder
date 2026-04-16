
#define MyAppName "MeetingRecorder"
#define MyAppVersion "4.0.0"
#define MyAppPublisher "LiveCaptionsHelper"
#define MyAppURL "https://github.com/LiveCaptionsHelper/SaveLiveCaptions"
#define MyAppExeName "MeetingRecorder.exe"
#define MyAppAUMID "MeetingRecorder.App"

[Setup]
AppId={{696FDCA2-CFAF-49EE-B803-EAE6FA86BA3E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no

OutputDir=installer_output
OutputBaseFilename=MeetingRecorder_Setup
SetupIconFile=assets\SaveLC.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern

; Require Lemonade Server to be installed (check for its binary)
; If not found, the setup will still proceed but the app will enter ERROR state
; on first launch. See README for Lemonade installation instructions.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Pack dist\MeetingRecorder directory produced by PyInstaller
Source: "dist\MeetingRecorder\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\MeetingRecorder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu group entry with AppUserModelID for consistent taskbar grouping
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppAUMID}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppAUMID}"; Tasks: desktopicon

[Run]
Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Filename: "{app}\{#MyAppExeName}"; Flags: nowait postinstall skipifsilent

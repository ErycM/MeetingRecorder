
; Version: CI may override via /dAppVersion=x.y.z. Manual builds fall back
; to reading __version__.py via Inno preprocessor GetStringFromFile.
#ifndef AppVersion
  #define AppVersion GetStringFromFile(AddBackslash(SourcePath) + "src\app\__version__.py")
  #define AppVersion Copy(AppVersion, Pos('"', AppVersion) + 1, 99999)
  #define AppVersion Copy(AppVersion, 1, Pos('"', AppVersion) - 1)
#endif
#define MyAppVersion AppVersion

#define MyAppName "MeetingRecorder"
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

; Per-user default (no UAC). Advanced tester can opt into per-machine via
; the wizard "Install for all users" button which prompts for elevation.
; ADR-9: PrivilegesRequired=lowest + PrivilegesRequiredOverridesAllowed=dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

OutputDir=installer_output
; Version-stamped output filename — every artifact carries its semver (G5/G6).
OutputBaseFilename=MeetingRecorder_Setup_v{#MyAppVersion}
SetupIconFile=assets\SaveLC.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern

; SignTool stub — activates only when CI passes /dSIGN=1 AND signtool.exe
; is on PATH with SIGNTOOL_CERT_PATH / SIGNTOOL_PASSWORD secrets set.
; Zero cost for v1 (unsigned). ADR-10.
#ifdef SIGN
  SignTool=signtool_cmd $f
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Startup-on-login opt-in task. Replaces install_startup.py (ADR-4). Unchecked
; by default so testers who don't want autostart aren't surprised.
Name: "startupicon"; Description: "Launch {#MyAppName} when I sign in to Windows"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Pack dist\MeetingRecorder directory produced by PyInstaller (onedir).
; The name 'MeetingRecorder' is pinned in MeetingRecorder.spec — risk #6.
Source: "dist\MeetingRecorder\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\MeetingRecorder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu group entry with AppUserModelID for consistent taskbar grouping
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppAUMID}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppAUMID}"; Tasks: desktopicon
; Startup shortcut under %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
; (per-user, works in both per-user and per-machine install modes). ADR-4.
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppAUMID}"; Tasks: startupicon

[Run]
Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Filename: "{app}\{#MyAppExeName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  LemonadePage: TOutputMsgMemoWizardPage;

function LemonadeBinaryOnPath(): Boolean;
var
  ResultCode: Integer;
begin
  // Best-effort: ask cmd "where" to find LemonadeServer.exe on PATH.
  // Non-zero exit = missing. Also checks common install locations implicitly
  // via PATH if the Lemonade installer added them.
  Result := Exec(
    ExpandConstant('{cmd}'),
    '/c where LemonadeServer.exe >nul 2>&1',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  ) and (ResultCode = 0);
end;

function LemonadeHttpProbe(): Boolean;
var
  WinHttp: Variant;
begin
  Result := False;
  try
    WinHttp := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    WinHttp.Open('GET', 'http://localhost:13305/api/v1/health', False);
    WinHttp.SetTimeouts(1000, 1000, 1000, 1000); // 1 second everywhere
    WinHttp.Send('');
    Result := (WinHttp.Status = 200);
  except
    Result := False;
  end;
end;

procedure InitializeWizard();
begin
  // Pre-create the informational page so ShouldSkipPage can decide whether
  // to show it. Created once; shown only if both Lemonade probes fail (S2).
  LemonadePage := CreateOutputMsgMemoPage(
    wpWelcome,
    'Lemonade Server prerequisite',
    'MeetingRecorder requires Lemonade Server for transcription',
    'Lemonade Server was not detected on this machine. You can continue '
    + 'the installation and install Lemonade later from lemonade-server.ai '
    + '— the app will show a reminder banner until Lemonade is reachable.',
    'Visit https://lemonade-server.ai to download the free installer, '
    + 'then re-launch MeetingRecorder. Click Next to continue the installation, '
    + 'or Cancel to exit.'
  );
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = LemonadePage.ID then
    // Skip the info page if EITHER probe succeeds (S3 path — Lemonade present).
    Result := LemonadeBinaryOnPath() or LemonadeHttpProbe();
end;

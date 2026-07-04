; QuizMaster Professional Installer
; Build the one-folder app first (either produces ..\dist\QuizMaster\):
;   PyInstaller:  scripts\build_quizmaster.ps1
;   Nuitka:       scripts\build_quizmaster_nuitka.ps1   (fully compiled)
; Then compile this file with Inno Setup Compiler, or pass -Installer to either
; build script to compile it automatically.

#define MyAppName "QuizMaster"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "LiveForge"
#define MyAppURL "https://liveforge.online"
#define MyAppExeName "QuizMaster.exe"

#ifndef ConsoleBuild
#define ConsoleBuild "0"
#endif

#if ConsoleBuild == "1"
#define MyOutputSuffix "-console"
#define MyLaunchDescription "Launch QuizMaster in console debug mode"
#else
#define MyOutputSuffix ""
#define MyLaunchDescription "Launch QuizMaster"
#endif

[Setup]
AppId={{7D2C8A99-0D6F-4D4A-93D0-3C9F2E9B64E8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\License.txt
OutputDir=..\installer\output
OutputBaseFilename=QuizMasterSetup-{#MyAppVersion}{#MyOutputSuffix}
SetupIconFile=..\core\assets\images\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "resetuserdata"; Description: "Reset saved login/session and local test data for this Windows user"; GroupDescription: "Clean install options:"; Flags: unchecked

[InstallDelete]
; Remove folders left behind by older builds before copying the new app.
Type: filesandordirs; Name: "{app}\core\assets"
Type: filesandordirs; Name: "{app}\core\server\static"
Type: filesandordirs; Name: "{app}\core\server\themes"
Type: filesandordirs; Name: "{app}\core\server\overlays"
Type: filesandordirs; Name: "{app}\core\quiz\html"
Type: filesandordirs; Name: "{app}\core\quiz\css"
Type: filesandordirs; Name: "{app}\core\quiz\js"
Type: filesandordirs; Name: "{app}\_internal\core\assets"
Type: filesandordirs; Name: "{app}\_internal\core\server\static"
Type: filesandordirs; Name: "{app}\_internal\core\server\themes"
Type: filesandordirs; Name: "{app}\_internal\core\server\overlays"
Type: filesandordirs; Name: "{app}\_internal\core\quiz\html"
Type: filesandordirs; Name: "{app}\_internal\core\quiz\css"
Type: filesandordirs; Name: "{app}\_internal\core\quiz\js"
; Main per-user runtime data location for fresh test installs.
Type: filesandordirs; Name: "{localappdata}\QuizMaster"; Tasks: resetuserdata
; Legacy cleanup for older dev/test builds that used Roaming AppData.
Type: filesandordirs; Name: "{userappdata}\QuizMaster"; Tasks: resetuserdata

[Files]
; One-folder PyInstaller output: install the whole app folder (exe + Qt/WebEngine
; runtime) into {app}. QuizMaster.exe sits at the root of dist\QuizMaster.
Source: "..\dist\QuizMaster\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\License.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_LICENSES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\QuizMaster"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\QuizMaster"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
#if ConsoleBuild == "1"
Name: "{group}\QuizMaster Console Debug"; Filename: "{cmd}"; Parameters: "/K ""cd /d """"{app}"""" && echo QuizMaster console debug && echo. && """"{app}\{#MyAppExeName}"""""""; WorkingDir: "{app}"
Name: "{autodesktop}\QuizMaster Console Debug"; Filename: "{cmd}"; Parameters: "/K ""cd /d """"{app}"""" && echo QuizMaster console debug && echo. && """"{app}\{#MyAppExeName}"""""""; WorkingDir: "{app}"; Tasks: desktopicon
#endif

[Run]
#if ConsoleBuild == "1"
Filename: "{cmd}"; Parameters: "/K ""cd /d """"{app}"""" && echo QuizMaster console debug && echo. && """"{app}\{#MyAppExeName}"""""""; WorkingDir: "{app}"; Description: "{#MyLaunchDescription}"; Flags: postinstall skipifsilent
#else
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyLaunchDescription}"; Flags: nowait postinstall skipifsilent
#endif

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\QuizMaster\cache"
Type: filesandordirs; Name: "{userappdata}\QuizMaster\cache"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

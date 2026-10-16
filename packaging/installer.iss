; Inno Setup script — builds ScribeOS-Setup.exe from the PyInstaller folder.
; 1) pyinstaller packaging/scribeos.spec --noconfirm --distpath build/dist --workpath build/work
; 2) "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss

#define AppName "ScribeOS"
#define AppVersion "2.0.0"
#define AppPublisher "BocchiTheTrader"

[Setup]
AppId={{6C1E9B4A-2F0E-4E4B-9D7B-5C1A2B3D4E5F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\build\installer
OutputBaseFilename=ScribeOS-Setup-{#AppVersion}
SetupIconFile=scribeos.ico
UninstallDisplayIcon={app}\ScribeOS.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\build\dist\ScribeOS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\ScribeOS.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ScribeOS.exe"; Tasks: desktopicon

[Registry]
; .scribez project archives open in ScribeOS (Project → Open archive)
Root: HKA; Subkey: "Software\Classes\.scribez"; ValueType: string; ValueName: ""; ValueData: "ScribeOS.Archive"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\ScribeOS.Archive"; ValueType: string; ValueName: ""; ValueData: "ScribeOS project archive"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\ScribeOS.Archive\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\ScribeOS.exe,0"

[Run]
Filename: "{app}\ScribeOS.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// WebView2 ships with Windows 10 (21H2+) and 11; warn on older systems.
function WebView2Installed(): Boolean;
var v: String;
begin
  Result := RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) or
            RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WebView2Installed() then
    MsgBox('ScribeOS needs the Microsoft Edge WebView2 Runtime. If the app does not open, install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703', mbInformation, MB_OK);
end;

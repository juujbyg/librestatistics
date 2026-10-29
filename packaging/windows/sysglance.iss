; Inno Setup script: installs the three sysglance editions, adds them to PATH
; and creates Start-menu shortcuts. Built by packaging/windows/build.ps1.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B0E2F4C-5E8A-4E8B-9C3B-1F1D5A7C2E90}
AppName=sysglance
AppVersion={#AppVersion}
AppPublisher=juujbyg
AppPublisherURL=https://github.com/juujbyg/sysglance
DefaultDirName={autopf}\sysglance
DefaultGroupName=sysglance
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist\windows
OutputBaseFilename=sysglance-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
ChangesEnvironment=yes
WizardStyle=modern

[Files]
Source: "..\..\dist\windows\sysglance.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\windows\sysglance-dev.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\windows\sysglance-classic.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"

[Icons]
Name: "{group}\sysglance"; Filename: "{app}\sysglance.exe"
Name: "{group}\sysglance (developer)"; Filename: "{app}\sysglance-dev.exe"
Name: "{group}\sysglance classic"; Filename: "{app}\sysglance-classic.exe"
Name: "{group}\Uninstall sysglance"; Filename: "{uninstallexe}"

[Tasks]
Name: "addtopath"; Description: "Add sysglance commands to PATH"; Flags: checkedonce

[Registry]
; Per-user install writes HKCU, admin install writes HKLM (HKA picks the right one).
Root: HKA; Subkey: "{code:EnvKey}"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function EnvKey(Param: String): String;
begin
  if IsAdminInstallMode then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else
    Result := 'Environment';
end;

function NeedsAddPath(Dir: String): Boolean;
var
  Paths: String;
  RootKey: Integer;
begin
  if IsAdminInstallMode then RootKey := HKEY_LOCAL_MACHINE else RootKey := HKEY_CURRENT_USER;
  if not RegQueryStringValue(RootKey, EnvKey(''), 'Path', Paths) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') = 0;
end;

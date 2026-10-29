; Inno Setup script: installs the three librestatistics editions, adds them to PATH
; and creates Start-menu shortcuts. Built by packaging/windows/build.ps1.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{399B8D3E-84B1-4FA4-821F-B30BA4740145}
AppName=LibreStatistics
AppVersion={#AppVersion}
AppPublisher=LibreProject
AppPublisherURL=https://github.com/LibreProject/librestatistics
DefaultDirName={autopf}\librestatistics
DefaultGroupName=LibreStatistics
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist\windows
OutputBaseFilename=librestatistics-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
ChangesEnvironment=yes
WizardStyle=modern

[Files]
Source: "..\..\dist\windows\librestatistics.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\windows\librestatistics-dev.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\windows\librestatistics-classic.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"

[Icons]
Name: "{group}\LibreStatistics"; Filename: "{app}\librestatistics.exe"
Name: "{group}\LibreStatistics (developer)"; Filename: "{app}\librestatistics-dev.exe"
Name: "{group}\LibreStatistics classic"; Filename: "{app}\librestatistics-classic.exe"
Name: "{group}\Uninstall LibreStatistics"; Filename: "{uninstallexe}"

[Tasks]
Name: "addtopath"; Description: "Add LibreStatistics commands to PATH"; Flags: checkedonce

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

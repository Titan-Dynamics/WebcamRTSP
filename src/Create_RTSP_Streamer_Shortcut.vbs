' Creates a desktop shortcut to the silent launcher with a custom icon.
Option Explicit

Dim oWS, fso, desktop, scriptDir, vbsLauncher, lnkPath, iconPath, shortcut
Set oWS = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

desktop   = oWS.SpecialFolders("Desktop")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
vbsLauncher = fso.BuildPath(scriptDir, "Run_RTSP_Streamer.vbs")
iconPath  = fso.BuildPath(scriptDir, "logo.ico")
lnkPath   = fso.BuildPath(desktop, "Webcam RTSP Streamer.lnk")

If Not fso.FileExists(vbsLauncher) Then
  WScript.Echo "Launcher not found: " & vbsLauncher
  WScript.Quit 1
End If

If Not fso.FileExists(iconPath) Then
  WScript.Echo "Icon not found: " & iconPath
  WScript.Quit 1
End If

Set shortcut = oWS.CreateShortcut(lnkPath)
shortcut.TargetPath = vbsLauncher
shortcut.WorkingDirectory = scriptDir
shortcut.IconLocation = iconPath
shortcut.Description = "Start Webcam RTSP Streamer (no console)"
shortcut.WindowStyle = 7 ' Minimized (no window shows for .vbs)
shortcut.Save

WScript.Echo "Shortcut created: " & lnkPath


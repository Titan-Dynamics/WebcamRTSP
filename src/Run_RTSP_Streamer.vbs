Set oWS = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Run from the folder this .vbs lives in
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
oWS.CurrentDirectory = scriptDir

' Prefer pythonw from local venvs, then PATH
py = ""
candidates = Array(".venv\Scripts\pythonw.exe", "venv\Scripts\pythonw.exe", "..\.venv\Scripts\pythonw.exe", "..\venv\Scripts\pythonw.exe")
For i = 0 To UBound(candidates)
  If fso.FileExists(scriptDir & "\" & candidates(i)) Then
    py = scriptDir & "\" & candidates(i)
    Exit For
  End If
Next
If py = "" Then
  py = "pythonw.exe"
End If

script = scriptDir & "\rtsp_streamer_gui.py"
cmd = """" & py & """ " & """" & script & """"

' 0 = hidden window, False = do not wait
oWS.Run cmd, 0, False


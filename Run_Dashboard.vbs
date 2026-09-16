Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
ParentDir = FSO.GetParentFolderName(ScriptDir)
Set WshShell = CreateObject("WScript.Shell")

PythonExe = ParentDir & "\.venv\Scripts\python.exe"
If Not FSO.FileExists(PythonExe) Then
    PythonExe = ScriptDir & "\.venv\Scripts\python.exe"
End If

SorterPy = ScriptDir & "\sorter.py"
WshShell.Run chr(34) & PythonExe & chr(34) & " " & chr(34) & SorterPy & chr(34), 0, False
Set WshShell = Nothing
Set FSO = Nothing

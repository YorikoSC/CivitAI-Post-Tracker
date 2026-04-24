Option Explicit

Dim shell, fso, scriptDir, trackerScript, pythonExe, args, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
trackerScript = fso.BuildPath(scriptDir, "tracker_app.py")

pythonExe = FindPythonExecutable()
If pythonExe = "" Then
    MsgBox "Python was not found. Please install Python 3.11+ and make sure Python Launcher or pythonw is available.", vbExclamation, "CivitAI Tracker"
    WScript.Quit 1
End If

args = BuildArgumentString()
command = Quote(pythonExe) & " " & Quote(trackerScript)
If args <> "" Then
    command = command & " " & args
End If

shell.CurrentDirectory = scriptDir
shell.Run command, 0, False

Function BuildArgumentString()
    Dim i, result
    result = ""
    For i = 0 To WScript.Arguments.Count - 1
        If result <> "" Then
            result = result & " "
        End If
        result = result & Quote(WScript.Arguments.Item(i))
    Next
    BuildArgumentString = result
End Function

Function FindPythonExecutable()
    Dim candidate

    candidate = FindOnPath("pyw.exe")
    If candidate <> "" Then
        FindPythonExecutable = candidate
        Exit Function
    End If

    candidate = FindOnPath("pythonw.exe")
    If candidate <> "" Then
        FindPythonExecutable = candidate
        Exit Function
    End If

    candidate = FindInCommonLocations("pythonw.exe")
    If candidate <> "" Then
        FindPythonExecutable = candidate
        Exit Function
    End If

    candidate = FindOnPath("py.exe")
    If candidate <> "" Then
        FindPythonExecutable = candidate
        Exit Function
    End If

    candidate = FindOnPath("python.exe")
    If candidate <> "" Then
        FindPythonExecutable = candidate
        Exit Function
    End If

    candidate = FindInCommonLocations("python.exe")
    If candidate <> "" Then
        FindPythonExecutable = candidate
        Exit Function
    End If

    FindPythonExecutable = ""
End Function

Function FindOnPath(fileName)
    Dim envPath, entries, i, folder, candidate
    envPath = shell.ExpandEnvironmentStrings("%PATH%")
    entries = Split(envPath, ";")
    For i = 0 To UBound(entries)
        folder = Trim(entries(i))
        If folder <> "" Then
            candidate = folder
            If Right(candidate, 1) <> "\" Then
                candidate = candidate & "\"
            End If
            candidate = candidate & fileName
            If fso.FileExists(candidate) Then
                FindOnPath = candidate
                Exit Function
            End If
        End If
    Next
    FindOnPath = ""
End Function

Function FindInCommonLocations(fileName)
    Dim roots, root, result, i
    roots = Array( _
        shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python"), _
        shell.ExpandEnvironmentStrings("%ProgramFiles%\Python"), _
        shell.ExpandEnvironmentStrings("%ProgramFiles(x86)%\Python") _
    )

    For i = 0 To UBound(roots)
        root = roots(i)
        If root <> "" And fso.FolderExists(root) Then
            result = FindNewestMatching(root, fileName)
            If result <> "" Then
                FindInCommonLocations = result
                Exit Function
            End If
        End If
    Next

    FindInCommonLocations = ""
End Function

Function FindNewestMatching(rootFolder, fileName)
    Dim folder, subFolder, candidate, bestPath
    bestPath = ""
    On Error Resume Next
    Set folder = fso.GetFolder(rootFolder)
    If Err.Number <> 0 Then
        Err.Clear
        On Error GoTo 0
        FindNewestMatching = ""
        Exit Function
    End If
    On Error GoTo 0

    candidate = fso.BuildPath(rootFolder, fileName)
    If fso.FileExists(candidate) Then
        bestPath = candidate
    End If

    For Each subFolder In folder.SubFolders
        candidate = fso.BuildPath(subFolder.Path, fileName)
        If fso.FileExists(candidate) Then
            bestPath = candidate
        End If
    Next

    FindNewestMatching = bestPath
End Function

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function

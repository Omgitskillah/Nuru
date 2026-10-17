"""HTTP Shortcuts schema 90; adapted from RemoteNotes android-shortcut.js.

Source: Omgitskillah/RemoteNotes, 04a2774ba9c72cd2599d7147822d51bb26b2fae6.
This preserves the legacy single-shot template. Request-aware templates are M4.
"""
from uuid import uuid4


def android_shortcut(origin: str, token: str) -> dict:
    return {"version": 90, "categories": [{"id": str(uuid4()), "name": "Remote Notes", "shortcuts": [{
        "id": str(uuid4()), "name": "Capture Obsidian Note", "method": "POST",
        "url": origin + "/v1/captures", "authentication": "bearer", "authToken": token,
        "requestBodyType": "custom_text", "contentType": "application/json", "bodyContent": "{{payload}}",
        "followRedirects": False, "acceptAllCertificates": False, "waitForInternet": False,
        "excludeFromHistory": True,
        "codeOnPrepare": r"""const note = prompt('First line: title. Following lines: note contents.', '', { multiline: true });
if (note === null) {
  abort();
} else {
  const parts = note.match(/^([^\r\n]*)(?:\r\n|\r|\n)([\s\S]*)$/);
  if (!parts || !parts[1].trim() || !parts[2].trim()) {
    alert('Enter a title on the first line and note contents on the following lines.');
    abort();
  } else {
    setVariable('payload', JSON.stringify({ content: '# ' + parts[1] + '\n\n' + parts[2] }));
  }
}""",
        "codeOnSuccess": """if (response.statusCode === 201 && JSON.parse(response.body).ok === true) {
  showToast('Saved to Obsidian');
} else {
  alert('The catcher did not confirm that the note was saved.');
}""",
        "codeOnFailure": """if (response && response.statusCode === 401) {
  alert('Capture access expired or was revoked. Ask the Mac operator to reissue this device and update the shortcut token. This request was not saved.');
} else {
  alert('The catcher did not confirm that the note was saved. Check your Inbox before retrying: this legacy shortcut cannot deduplicate a retry.');
}""",
        "responseHandling": {"uiType": "dialog", "successOutput": "none", "failureOutput": "detailed"},
    }]}]}

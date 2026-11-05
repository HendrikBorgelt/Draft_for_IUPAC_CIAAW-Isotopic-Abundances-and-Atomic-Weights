// Opens each example query in its own tab of the query editor. The editor's <sparql-editor> element
// carries the examples as JSON in its data-examples attribute, and the editor is built on YASGUI, whose
// tabs are stored in the browser. A tab with the same id is reused, so the example's query is set again
// on each visit and the tabs the reader made are kept.
const editor = document.querySelector("sparql-editor[data-examples]");

if (editor) {
  await customElements.whenDefined("sparql-editor");
  const examples = JSON.parse(editor.dataset.examples);
  const yasgui = editor.yasgui;
  for (const example of examples) {
    if (yasgui.getTab(example.id)) {
      // A tab restored from storage has no editor until it is drawn, and only selecting it draws it.
      yasgui.selectTabId(example.id);
      yasgui.getTab(example.id).setQuery(example.query);
    } else {
      yasgui.addTab(false, { id: example.id, name: example.name, yasqe: { value: example.query } });
    }
  }
  yasgui.selectTabId(examples[0].id);
}

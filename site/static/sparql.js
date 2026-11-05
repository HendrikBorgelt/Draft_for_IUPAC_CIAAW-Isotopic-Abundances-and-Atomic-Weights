// Query form of the SPARQL page. This file is only loaded when an endpoint is configured.
// Everything from a response is written with textContent, so none of it is parsed as HTML.
const form = document.getElementById("sparql-form");
const input = document.getElementById("sparql-query");
const status = document.getElementById("sparql-status");
const output = document.getElementById("sparql-results");
const examples = [...document.querySelectorAll("code.language-sparql")];

// The worker answers CONSTRUCT and DESCRIBE only when asked for an RDF format.
function isGraphQuery(query) {
  const body = query.replace(/(PREFIX|BASE)\s+[^<]*<[^>]*>/gi, "").replace(/#.*$/gm, "");
  return /^\s*(CONSTRUCT|DESCRIBE)\b/i.test(body);
}

function cell(tag, text) {
  const element = document.createElement(tag);
  element.textContent = text;
  return element;
}

function resultTable(results) {
  const table = document.createElement("table");
  const head = table.createTHead().insertRow();
  results.head.vars.forEach((name) => head.append(cell("th", name)));
  const body = table.createTBody();
  results.results.bindings.forEach((binding) => {
    const row = body.insertRow();
    results.head.vars.forEach((name) => row.append(cell("td", binding[name]?.value ?? "")));
  });
  return table;
}

async function run(event) {
  event.preventDefault();
  const graph = isGraphQuery(input.value);
  status.textContent = "Running…";
  output.replaceChildren();
  try {
    const response = await fetch(form.dataset.endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/sparql-query",
        Accept: graph ? "text/turtle" : "application/sparql-results+json",
      },
      body: input.value,
    });
    const text = await response.text();
    if (!response.ok) {
      status.textContent = `Error ${response.status}: ${text}`;
    } else if (graph) {
      status.textContent = "Done.";
      output.append(cell("pre", text));
    } else {
      const results = JSON.parse(text);
      if ("boolean" in results) {
        status.textContent = `Answer: ${results.boolean}`;
      } else {
        const count = results.results.bindings.length;
        status.textContent = `${count} ${count === 1 ? "result" : "results"}.`;
        output.append(resultTable(results));
      }
    }
  } catch (error) {
    status.textContent = `Could not reach the endpoint: ${error.message}`;
  }
}

form.addEventListener("submit", run);

if (examples.length > 0) {
  input.value = examples[0].textContent;
}

examples.forEach((example) => {
  const button = cell("button", "Use this query");
  button.type = "button";
  button.addEventListener("click", () => {
    input.value = example.textContent;
    form.scrollIntoView({ behavior: "smooth" });
  });
  example.parentElement.after(button);
});

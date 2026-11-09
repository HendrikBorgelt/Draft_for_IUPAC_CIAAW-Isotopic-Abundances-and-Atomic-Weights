// Formats the DOIs of the sources as APA with citation.js, and caches the results in
// site/references.json (DOI -> HTML). build_site.py uses it for the Bibliography on the element pages.
// Only DOIs not yet in the cache are looked up. Same approach as updateReferences.js in the blog.
//
//   node scripts/update_references.js data/example-output.ttl data/example-output-next.ttl 10.1186/1758-2946-3-37
//
// An argument that starts with "10." is a DOI to format, a .json argument is a comparison JSON to scan for the
// "doi" of its sources, any other argument is a Turtle file to scan.
//
// DOIs are found in the Turtle as links <https://doi.org/10.xxxx/yyyy>: the rdfs:seeAlso of each report.

const fs = require('fs')

const { Cite } = require('@citation-js/core')
require('@citation-js/plugin-doi')
require('@citation-js/plugin-csl')

const cacheFile = 'site/references.json'
const cache = fs.existsSync(cacheFile) ? JSON.parse(fs.readFileSync(cacheFile, 'utf8')) : {}

// DOIs start with "10."; as in the blog, the cache is keyed on the upper-case DOI
const dois = new Set()
for (const file of process.argv.slice(2)) {
  if (file.startsWith('10.')) {
    dois.add(file.toUpperCase())
    continue
  }
  // Turtle has <https://doi.org/10.x/y>, the JSON of the comparisons has "doi": "10.x/y"
  const pattern = file.endsWith('.json') ? /"doi": "(10\.[^"]+)"/g : /doi\.org\/(10\.[^>\s"]+)/g
  for (const match of fs.readFileSync(file, 'utf8').matchAll(pattern)) {
    dois.add(match[1].toUpperCase())
  }
}

async function main () {
  const missing = [...dois].filter(doi => !(doi in cache))
  console.log(`${dois.size} DOIs, ${missing.length} not yet cached`)
  for (const doi of missing) {
    try {
      const cite = await Cite.async(doi)
      const html = cite.format('bibliography', { format: 'html', template: 'apa' })
      // keep only the entry itself, without the csl-bib-body and csl-entry wrappers
      cache[doi] = html.match(/class="csl-entry">([\s\S]*)<\/div>\s*<\/div>\s*$/)[1].trim()
      console.log(`Formatted ${doi}`)
    } catch (error) {
      console.error(`Failed ${doi}: ${error.message}`)
    }
  }
  const sorted = Object.fromEntries(Object.keys(cache).sort().map(doi => [doi, cache[doi]]))
  fs.writeFileSync(cacheFile, JSON.stringify(sorted, null, 2) + '\n')
}

main()

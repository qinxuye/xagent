import type { Parents, Root } from 'mdast'
import type { Plugin } from 'unified'

/**
 * GFM parses surplus cells, but the HTML conversion silently discards them.
 * Preserve a mismatched table as inert source before that conversion. Do not
 * infer which pipes were intended as text or shift values between columns.
 */
export const remarkPreserveTableContent: Plugin<[], Root> = () => (tree, file) => {
  const source = String(file)

  function walk(parent: Parents) {
    parent.children.forEach((node, index) => {
      if (node.type === 'table') {
        const columns = node.children[0]?.children.length
        if (node.children.some((row) => row.children.length !== columns)) {
          const start = node.position?.start.offset
          const end = node.position?.end.offset
          // Parsed Markdown always has offsets; leave synthetic plugin nodes
          // alone when there is no original source to preserve.
          if (start !== undefined && end !== undefined) {
            parent.children[index] = {
              type: 'code',
              value: source.slice(start, end),
              position: node.position,
            }
          }
        }
      } else if ('children' in node) {
        walk(node)
      }
    })
  }

  walk(tree)
}

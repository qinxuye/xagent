import remarkMath from 'remark-math'
import type { Plugin } from 'unified'

/** Keep ordinary prices from opening math while retaining $math$ and $$math$$. */
export const remarkCurrencySafeMath: Plugin = function () {
  remarkMath.call(this)

  // Extend remark-math's tokenizer rather than rewriting Markdown source: code,
  // escapes, link destinations, and display math must retain their own parsing.
  const extensions = this.data().micromarkExtensions
  const text = extensions?.[extensions.length - 1].text?.[36]
  const constructs = Array.isArray(text) ? text : text ? [text] : []

  for (const construct of constructs) {
    if (construct.name !== 'mathText') continue
    const tokenize = construct.tokenize

    construct.tokenize = function (effects, ok, nok) {
      const start = this.now()

      return tokenize.call(this, effects, (code) => {
        const source = this.sliceSerialize({ start, end: this.now() })

        // A single-dollar opener/closer must touch non-whitespace content, and
        // a closer cannot precede a digit (the next price in "$10–$25"). Reject
        // the entire attempt so Markdown can still parse intervening emphasis
        // and links. Explicit multi-dollar math keeps remark-math's behavior.
        if (!source.startsWith('$$') && (
          /^\$\s|\s\$$/.test(source) ||
          (code !== null && code >= 48 && code <= 57)
        )) {
          return nok(code)
        }

        return ok(code)
      }, nok)
    }
  }
}

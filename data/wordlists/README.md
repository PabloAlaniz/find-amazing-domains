# Bundled word lists

Common words, one per line, lowercase ASCII letters only, at least three
letters, profanities removed by hand. They are the input of the weekly report in `reports/` and a better
starting point than the twenty-word samples in `data/samples/`.

| File | Words | Source |
|---|---|---|
| `en-common.txt` | 2,840 | the 3,000 most frequent English words in [wordfreq](https://github.com/rspeer/wordfreq) 3.x, minus words with digits, apostrophes or fewer than three letters, and a handful of profanities |
| `es-common.txt` | 2,383 | the same for Spanish; accented words are left out, so these lists exercise ASCII hacks only |

Regenerate with:

```bash
python -c '
import re
from wordfreq import top_n_list
for lang in ("en", "es"):
    words = [w for w in top_n_list(lang, 3000) if re.fullmatch(r"[a-z]{3,}", w)]
    open(f"data/wordlists/{lang}-common.txt", "w").write("\n".join(words) + "\n")
'
```

The frequency data comes from wordfreq, whose word lists are distributed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); these two files
are derived from them and carry the same licence.

# Testing

On GitHub: Actions → Tests and demo → Run workflow → main → Run workflow. Expand each job's tests and sample scenario.

Locally: `python -m unittest discover -s tests -v`, then `python demo.py`.

The demo uses synthetic actors and data. Role checks verify the domain rules; they are not a login system.

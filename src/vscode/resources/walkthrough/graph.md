# Explore the reference graph

Strata documents point at each other by kind and name: a deployment names its workspace, a
workspace names its providers and modules, and so on. The **Graph** view (Strata icon in the
Activity Bar) shows every one of those links.

- One entry per document, for example `workspace/main`
- Expand it to see each reference it makes: the field, and the document it points to
- References that don't resolve are flagged **unresolved**
- Click a reference to jump to the exact field in the file

Use it to answer "what breaks if I rename this?" before you rename it.

> **Tip:** the graph refreshes when you ask. Use the refresh icon in the view's title bar after
> larger edits.

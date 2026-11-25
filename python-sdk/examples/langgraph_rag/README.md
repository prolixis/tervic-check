# LangGraph RAG: a retriever that silently returns nothing

`broken.py` filters documents on a key that does not exist, so the retriever returns `[]`. Nothing
raises: the answer node calls the model with an empty context and the model still answers.

```bash
python examples/langgraph_rag/broken.py
tervic check          # exit 1: TV301 empty-retrieval + TV302 retrieval-unused, root cause `retrieve`
tervic fix            # ready-to-paste prompt for Claude Code / Cursor

python examples/langgraph_rag/fixed.py
tervic check          # exit 0
```

The model is LangChain's `FakeListChatModel` replaying the recorded answer in `cassette.json`, so
the example needs no API key. Tervic is attached with one line: `tervic.attach(graph.compile())`.

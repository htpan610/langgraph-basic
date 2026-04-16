from langchain_tavily import TavilySearch

tool=TavilySearch(max_results=2)
tools=[tool]
tools.invoke("what is the capital of France?")
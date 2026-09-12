import os
from openai import OpenAI

client = OpenAI(
    api_key="sk-4ce2392144cf45e1b01bacd1dde3250b",
    base_url="https://llm-saze3h4ed66uof1o.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)
completion = client.chat.completions.create(
    model="qwen3.8-max",
    messages=[{'role': 'user', 'content': '你是谁？'}]
)
print(completion.choices[0].message.content)
'use strict';
const ModelAccess = {
  frameworks: {ollama: 'Ollama Python', openai: 'OpenAI-compatible Python', langchain: 'LangChain', llamaindex: 'LlamaIndex'},
  example(framework, model, baseUrl, contextLength, target = '') {
    if (!Object.hasOwn(this.frameworks, framework) || !model || !baseUrl) return null;
    // JSON string literals are also valid Python literals for these strings.
    const name = JSON.stringify(model), url = JSON.stringify(baseUrl.replace(/\/$/, ''));
    const context = Number.isSafeInteger(contextLength) && contextLength > 0 ? contextLength : 4096;
    const prompt = 'Say hello in one sentence.';
    const secure = baseUrl.includes('/inference');
    const auth = secure ? `import os\nimport httpx\n\n# Trust the Llameros certificate; never disable TLS verification.\nos.environ["SSL_CERT_FILE"] = os.environ["LLAMEROS_CA_CERT"]\nheaders = {"Authorization": "Bearer " + os.environ["LLAMEROS_API_KEY"],\n           "X-Llameros-Target": ${JSON.stringify(target || 'cpu')}}\n\n` : '';
    const examples = {
      ollama: {install: 'python -m pip install ollama', code: `from ollama import Client

client = Client(host=${url}, timeout=120.0${secure ? ', headers=headers' : ''})
response = client.chat(
    model=${name},
    messages=[{"role": "user", "content": "${prompt}"}],
    options={"num_ctx": ${context}, "num_predict": 256},
)
print(response.message.content)
`},
      openai: {install: 'python -m pip install openai', code: `from openai import OpenAI

client = OpenAI(
    base_url=${JSON.stringify(baseUrl.replace(/\/$/, '') + '/v1')},
    api_key=${secure ? 'os.environ["LLAMEROS_API_KEY"]' : '"ollama",  # Client requires a value; local Ollama ignores it.'}${secure ? ',' : ''}
${secure ? '    default_headers=headers,\n    http_client=httpx.Client(verify=os.environ["LLAMEROS_CA_CERT"]),' : ''}
    timeout=120.0,
)
response = client.chat.completions.create(
    model=${name},
    messages=[{"role": "user", "content": "${prompt}"}],
    max_tokens=256,
)
print(response.choices[0].message.content)
`},
      langchain: {install: 'python -m pip install langchain-ollama', code: `from langchain_ollama import ChatOllama

llm = ChatOllama(
    model=${name},
    base_url=${url},
    num_ctx=${context},
    num_predict=256,
${secure ? '    client_kwargs={"headers": headers},\n    validate_model_on_init=False,' : ''}
)
response = llm.invoke("${prompt}")
print(response.content)
`},
      llamaindex: {install: 'python -m pip install llama-index-llms-ollama', code: `from llama_index.llms.ollama import Ollama

llm = Ollama(
    model=${name},
    base_url=${url},
    request_timeout=120.0,
    context_window=${context},
    additional_kwargs={"num_predict": 256},
${secure ? '    headers=headers,' : ''}
)
response = llm.complete("${prompt}")
print(response.text)
`},
    };
    const example = examples[framework];
    if (secure) example.code = auth + example.code;
    return example;
  },
};
if (typeof module !== 'undefined') module.exports = ModelAccess;
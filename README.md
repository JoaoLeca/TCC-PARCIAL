# Assistente da Barbearia Imperial

Protótipo acadêmico de atendimento com agenda, API, interface web e busca RAG integrada ao GPT. O backend valida horários e impede conflitos antes de gravar. A interface e o serviço rodam em Windows, macOS e Linux com Python 3.11 ou 3.12; também há Docker para execução multiplataforma.

## Dados usados

Os parâmetros vieram de `ParametrosBarbearia.docx`: Barbearia Imperial, Ribeirão Preto-SP; Rafael (terça a domingo), Diego (terça a sábado), Alecio (segunda a sexta); quatro serviços e dois pacotes. A base de conhecimento tem 15 documentos curtos. O telefone e endereço foram fornecidos como dados de demonstração.

Por padrão, clientes podem agendar e remarcar online de 1 a 7 dias de antecedência. O calendário informa o período aberto. Em instalações existentes com `.env` próprio, defina `MAX_BOOKING_DAYS=7` e reinicie a API e a interface para aplicar a nova janela.

## Executar localmente

Use Python 3.11 ou 3.12. A opção recomendada é `uv`, que usa o arquivo `uv.lock` para instalar as mesmas versões em Windows, macOS e Linux e pode baixar uma versão compatível do Python quando necessário:

```bash
python -m pip install uv
uv sync --python 3.12
```

Se preferirem usar o `venv` e o `pip`, criem o ambiente com Python 3.12, ativem `.venv` e executem `pip install .`.

Copie `.env.example` para `.env` (`Copy-Item .env.example .env` no PowerShell ou `cp .env.example .env` no macOS/Linux). O protótipo de agenda e consultas funciona sem chave. Para ativar GPT e busca RAG, crie uma chave na plataforma da OpenAI e insira-a localmente em `OPENAI_API_KEY` no `.env`. A chave não deve ser enviada ao GitHub. Depois, rode:

```bash
uv run uvicorn barber_bot.api:app --app-dir src --reload
```

Em outro terminal, com o mesmo ambiente virtual:

```bash
uv run streamlit run src/streamlit_app.py
```

Abra o endereço local exibido pelo Streamlit. A API fica em `http://127.0.0.1:8000/docs`.

## Criar índice da base de conhecimento

Depois de configurar a chave GPT, rode na pasta do projeto:

```bash
python -c "from barber_bot.knowledge import ingest_knowledge; print(ingest_knowledge())"
```

O índice local usa Chroma e trechos de até 700 tokens com sobreposição de 100. A busca gera embeddings pela API da OpenAI.

## Docker

Com Docker instalado e `.env` configurado, inicie a aplicação:

```bash
docker compose up --build
```

A interface abre em `http://localhost:8501` e a documentação da API em `http://localhost:8000/docs`. A pasta `data/` fica montada para preservar o banco e o índice entre reinicializações.

## Testes e avaliação

```bash
uv run pytest
```

Os testes cobrem conflito de agenda, confirmação explícita, duração fora do expediente, agendamento inexistente e escala semanal. A pasta `eval/` inclui 150 frases rotuladas em dez intenções, 30 cenários FCR, um baseline por palavras-chave, um baseline GPT sem RAG e os cálculos de acurácia, F1 macro, FCR, latência média e Likert.

Com a chave GPT configurada, rode o classificador no conjunto de intenções e calcule as métricas:

```bash
uv run python eval/run_intent_eval.py
uv run python eval/export_logs.py --out eval/latencias.csv
uv run python eval/metrics.py --intents eval/resultados_intencao.csv --fcr eval/cenarios_fcr.csv --latency eval/latencias.csv --likert eval/respostas_formulario.csv
```

`run_intent_eval.py` faz chamadas ao GPT para cada frase e pode gerar custo de API. O script `export_logs.py` cria o CSV de latências e horários; o relatório calcula latência média por resposta e duração média da sessão. Para Likert, use o questionário em `eval/questionario_likert.md`, exporte respostas sem e-mail e nomeie a coluna do item escolhido como `rating`. Nos cenários FCR, dois avaliadores registram suas avaliações nos campos individuais e depois preenchem os campos de consenso usados no cálculo. O projeto não fabrica dados de voluntários.

O baseline por palavras-chave está em `eval/baselines.py::keyword_faq`; o baseline GPT sem recuperação está em `eval/baselines.py::llm_without_rag`; o protótipo com RAG e ferramentas é acessado pela rota `/chat` da API. Usem as mesmas perguntas e as mesmas regras de avaliação para os três modos.

## Estrutura

```text
data/knowledge/     documentos da base RAG
src/barber_bot/     banco, regras, busca, agente e API
src/streamlit_app.py interface web
tests/              testes da lógica de agenda
eval/               dataset, baselines e métricas
```

## Configuração antes da banca

- Confirmar se preço e duração dos serviços continuam válidos.
- Definir e substituir telefone/endereço de demonstração, se necessário.
- Confirmar se a multa de 30% por falta pode ser aplicada e como comunicá-la.
- A chave de API e o uso de embeddings podem gerar custos na conta OpenAI.
- Consultar a orientadora sobre consentimento e eventual aprovação ética antes de recrutar voluntários.
- Fazer deploy público somente após revisar privacidade, autenticação, rate limits e segurança do banco. A configuração atual é uma demo acadêmica local.

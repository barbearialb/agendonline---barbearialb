import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from google.cloud.firestore_v1.field_path import FieldPath
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
import google.api_core.retry as retry
import random
import pandas as pd
import time
from PIL import Image, ImageDraw, ImageFont
import io

st.set_page_config(
    page_title="Agendamentos-Barbearia Lucas Borges",
    page_icon="icone_barbearia.png"
)


st.markdown(
    """
    <style>
        /* --- ESTILOS DE FONTE E SEUS ESTILOS ORIGINAIS --- */
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
        html, body, [class*="st-"], [class*="css-"] { font-family: 'Roboto', sans-serif; }
        table { display: block !important; width: fit-content !important; }
        div[data-testid="stForm"] { display: block !important; }

        /* --- CÓDIGO FINAL E VENCEDOR PARA OS BOTÕES --- */

        /* --- BOTÃO VERDE (CONFIRMAR AGENDAMENTO) --- */
        /* Alvo: O botão exato que o seu navegador indicou */
        #root > div:nth-child(1) > div.withScreencast > div > div > section > div.stMainBlockContainer.block-container.st-emotion-cache-mtjnbi.eht7o1d4 > div > div > div > div:nth-child(8) > div > div > div > div > div > div > button {
            background-color: #28a745 !important;
            border-color: #28a745 !important;
        }
        /* Alvo: O texto dentro do botão verde */
        #root > div:nth-child(1) > div.withScreencast > div > div > section > div.stMainBlockContainer.block-container.st-emotion-cache-mtjnbi.eht7o1d4 > div > div > div > div:nth-child(8) > div > div > div > div > div > div > button p {
            color: white !important;
        }

        /* --- BOTÃO VERMELHO (CANCELAR AGENDAMENTO) --- */
        /* Alvo: O segundo botão exato que o seu navegador indicou */
        #root > div:nth-child(1) > div.withScreencast > div > div > section > div.stMainBlockContainer.block-container.st-emotion-cache-mtjnbi.eht7o1d4 > div > div > div > div:nth-child(9) > div > div > div > div > div > div > button {
            background-color: #dc3545 !important;
            border-color: #dc3545 !important;
        }
        /* Alvo: O texto dentro do botão vermelho */
        #root > div:nth-child(1) > div.withScreencast > div > div > section > div.stMainBlockContainer.block-container.st-emotion-cache-mtjnbi.eht7o1d4 > div > div > div > div:nth-child(9) > div > div > div > div > div > div > button p {
            color: white !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# Carregar as credenciais do Firebase e-mail a partir do Streamlit secrets
FIREBASE_CREDENTIALS = None
EMAIL = None
SENHA = None

try:
    # Carregar credenciais do Firebase
    firebase_credentials_json = st.secrets["firebase"]["FIREBASE_CREDENTIALS"]
    FIREBASE_CREDENTIALS = json.loads(firebase_credentials_json)

    # Carregar credenciais de e-mail
    EMAIL = st.secrets["email"]["EMAIL_CREDENCIADO"]
    SENHA = st.secrets["email"]["EMAIL_SENHA"]

except KeyError as e:
    st.error(f"Chave ausente no arquivo secrets.toml: {e}")
except json.JSONDecodeError as e:
    st.error(f"Erro ao decodificar as credenciais do Firebase: {e}")
except Exception as e:
    st.error(f"Erro inesperado: {e}")

# Inicializar Firebase com as credenciais
if FIREBASE_CREDENTIALS:
    if not firebase_admin._apps:  # Verifica se o Firebase já foi inicializado
        try:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"Erro ao inicializar o Firebase: {e}")


# Obter referência do Firestore
db = firestore.client() if firebase_admin._apps else None

# Dados básicos
# A lista de horários base será gerada dinamicamente na tabela

servicos = {
    "Tradicional",
    "Social",
    "Degradê",
    "Pezim",
    "Navalhado",
    "Barba",
    "Abordagem de visagismo",
    "Consultoria de visagismo",
}

# Lista de serviços para exibição
lista_servicos = servicos

barbeiros = ["Aluizio", "Lucas Borges"]

# Função para enviar e-mail
def enviar_email(assunto, mensagem):
    # Proteção extra para caso as credenciais não carreguem
    if not EMAIL or not SENHA:
        st.warning("Credenciais de e-mail não configuradas. E-mail não enviado.")
        return
    try:
        msg = MIMEText(mensagem)
        msg['Subject'] = assunto
        msg['From'] = EMAIL
        msg['To'] = EMAIL

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL, SENHA)  # Login usando as credenciais do e-mail
            server.sendmail(EMAIL, EMAIL, msg.as_string())
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")

# SUBSTITUA A FUNÇÃO INTEIRA
def salvar_agendamento(data_str, horario, nome, telefone, servicos, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False

    try:
        # Converte a data string (que vem do formulário) para um objeto datetime
        data_obj = datetime.strptime(data_str, '%d/%m/%Y')
        
        # Cria o ID do documento no formato correto YYYY-MM-DD
        data_para_id = data_obj.strftime('%Y-%m-%d')
        chave_agendamento = f"{data_para_id}_{horario}_{barbeiro}"
        agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
        
        # Esta é a parte que você perguntou, agora dentro da função principal
        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                # Se o documento já existe, a transação falha para evitar agendamento duplo
                raise ValueError("Horário já ocupado por outra pessoa.")
            
            # Se o horário estiver livre, a transação define os novos dados
            transaction.set(doc_ref, {
                'data': data_obj,
                'horario': horario,
                'nome': nome,
                'telefone': telefone,
                'servicos': servicos,
                'barbeiro': barbeiro,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
        
        # Executa a transação
        transaction = db.transaction()
        update_in_transaction(transaction, agendamento_ref)
        return True # Retorna sucesso

    except ValueError as e:
        # Captura o erro "Horário já ocupado" e exibe ao utilizador
        st.error(f"Erro ao agendar: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao salvar o agendamento: {e}")
        return False

# Função para cancelar agendamento no Firestore
def cancelar_agendamento(doc_id, telefone_cliente):
    """
    Cancela um agendamento no Firestore de forma segura.
    """
    if not db:
        st.error("Firestore não inicializado.")
        return None
    
    try:
        doc_ref = db.collection('agendamentos').document(doc_id)
        doc = doc_ref.get()

        # PASSO CHAVE: VERIFICA SE O DOCUMENTO EXISTE ANTES DE TUDO
        if not doc.exists:
            st.error(f"Nenhum agendamento encontrado com o ID: {doc_id}")
            return "not_found" # Retorna um código de erro

        agendamento_data = doc.to_dict()
        telefone_no_banco = agendamento_data.get('telefone', '') # Pega o telefone de forma segura

        # Compara os telefones
        if telefone_no_banco.replace(" ", "").replace("-", "") != telefone_cliente.replace(" ", "").replace("-", ""):
            st.error("O número de telefone não corresponde ao agendamento.")
            return "phone_mismatch" # Retorna outro código de erro

        # Se tudo deu certo, deleta e retorna os dados
        doc_ref.delete()
        return agendamento_data

    except Exception as e:
        st.error(f"Ocorreu um erro ao tentar cancelar: {e}")
        return None

# no seu arquivo si (9).py

def desbloquear_horario(data_para_id, horario, barbeiro):
    """
    Desbloqueia um horário usando a data já no formato correto (YYYY-MM-DD).
    """
    if not db:
        st.error("Firestore não inicializado. Não é possível desbloquear.")
        return

    # A função agora recebe a data JÁ no formato YYY-MM-DD, então não precisa converter.
    # As linhas que causavam o erro foram removidas.
    
    chave_bloqueio = f"{data_para_id}_{horario}_{barbeiro}_BLOQUEADO"
    agendamento_ref = db.collection('agendamentos').document(chave_bloqueio)
    
    try:
        # Tenta apagar o documento de bloqueio diretamente.
        # Se o documento não existir, o Firestore não faz nada e não gera erro.
        agendamento_ref.delete()
        # A mensagem de sucesso agora é mostrada na tela principal.

    except Exception as e:
        st.error(f"Erro ao tentar desbloquear o horário seguinte: {e}")

# SUBSTITUA A FUNÇÃO INTEIRA PELA VERSÃO ABAIXO:
# ESTA É A VERSÃO CORRETA E FINAL DA FUNÇÃO
# (Pode substituir a sua inteira por esta)

def buscar_agendamentos_e_bloqueios_do_dia(data_obj):
    """
    Busca todos os agendamentos e bloqueios do dia e retorna um DICIONÁRIO
    com os dados completos de cada um. A chave é o ID do documento.
    """
    if not db:
        st.error("Firestore não inicializado.")
        # MUDANÇA 1: Em caso de erro, retorna um dicionário vazio {}
        return {} 

    # MUDANÇA 2: Inicializamos um DICIONÁRIO vazio, e não um set.
    ocupados_map = {} 
    prefixo_id = data_obj.strftime('%Y-%m-%d')

    try:
        # A sua consulta ao Firestore está PERFEITA!
        docs = db.collection('agendamentos') \
                 .order_by(FieldPath.document_id()) \
                 .start_at([prefixo_id]) \
                 .end_at([prefixo_id + '\uf8ff']) \
                 .stream()
        
        # Populamos o dicionário com os dados completos
        for doc in docs:
            ocupados_map[doc.id] = doc.to_dict()

    except Exception as e:
        st.error(f"Erro ao buscar agendamentos do dia: {e}")

    # MUDANÇA 3: Retornamos o dicionário que criamos.
    return ocupados_map

# A SUA FUNÇÃO, COM A CORREÇÃO DO NOME DA VARIÁVEL
def verificar_disponibilidade_horario_seguinte(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False

    try:
        horario_dt = datetime.strptime(horario, '%H:%M')
        horario_seguinte_dt = horario_dt + timedelta(minutes=30)
        if horario_seguinte_dt.hour >= 20:
            return False 

        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
        data_obj = datetime.strptime(data, '%d/%m/%Y')
        data_para_id = data_obj.strftime('%Y-%m-%d')

        # --- A CORREÇÃO ESTÁ AQUI ---
        # O nome da variável foi padronizado para "chave_agendamento_seguinte"
        chave_agendamento_seguinte = f"{data_para_id}_{horario_seguinte_str}_{barbeiro}"
        agendamento_ref_seguinte = db.collection('agendamentos').document(chave_agendamento_seguinte)
        # --- FIM DA CORREÇÃO ---

        chave_bloqueio_seguinte = f"{data_para_id}_{horario_seguinte_str}_{barbeiro}_BLOQUEADO"
        bloqueio_ref_seguinte = db.collection('agendamentos').document(chave_bloqueio_seguinte)

        doc_agendamento_seguinte = agendamento_ref_seguinte.get()
        doc_bloqueio_seguinte = bloqueio_ref_seguinte.get()

        return not doc_agendamento_seguinte.exists and not doc_bloqueio_seguinte.exists

    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore ao verificar horário seguinte: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade do horário seguinte: {e}")
        return False

# NOVA FUNÇÃO PARA GERAR A IMAGEM DE RESUMO
def gerar_imagem_resumo(nome, data, horario, barbeiro, servicos):
    """
    Gera uma imagem de resumo do agendamento.

    Args:
        nome (str): Nome do cliente.
        data (str): Data do agendamento (ex: "22/08/2025").
        horario (str): Horário do agendamento (ex: "10:30").
        barbeiro (str): Nome do barbeiro.
        servicos (list): Lista de serviços selecionados.

    Returns:
        bytes: A imagem gerada em formato PNG como bytes, pronta para download.
    """
    try:
        template_path = "template_resumo.png"  # <-- LINHA CORRIGIDA
        font_path = "font.ttf"
        img = Image.open(template_path).convert("RGBA") # Adicionado .convert("RGBA") para melhor compatibilidade com PNG
        draw = ImageDraw.Draw(img)
        
        # 1. Defina a largura máxima em pixels que o nome pode ocupar.
        LARGURA_MAXIMA_NOME = 800

        # 2. Defina o tamanho inicial e o tamanho mínimo da fonte.
        tamanho_fonte_nome = 85  # Começa com o tamanho que você gostou
        tamanho_fonte_minimo = 30 

        # 3. Carrega a fonte com o tamanho inicial.
        font_nome = ImageFont.truetype(font_path, tamanho_fonte_nome)

        # 4. Loop para reduzir o tamanho da fonte se o nome for muito largo.
        while font_nome.getbbox(nome)[2] > LARGURA_MAXIMA_NOME and tamanho_fonte_nome > tamanho_fonte_minimo:
            tamanho_fonte_nome -= 5 
            font_nome = ImageFont.truetype(font_path, tamanho_fonte_nome)

        # Carrega a fonte para o corpo do texto (esta linha continua existindo).
        font_corpo = ImageFont.truetype(font_path, 65)

        # 2. Formata o texto do resumo
        # Junta a lista de serviços em uma única string, com quebra de linha se for longa
        servicos_str = ", ".join(servicos)
        if len(servicos_str) > 30: # Se a linha de serviços for muito longa
            servicos_formatados = '\n'.join(servicos) # Coloca um serviço por linha
            texto_resumo = f"""
Data: {data}
Horário: {horario}
Barbeiro: {barbeiro}
Serviços:
{servicos_formatados}
"""
        else:
            texto_resumo = f"""
Data: {data}
Horário: {horario}
Barbeiro: {barbeiro}
Serviços: {servicos_str}
"""

        # 3. Define a posição e as cores do texto
        #    (X, Y) -> Distância da esquerda, Distância do topo
        #    VOCÊ PROVAVELMENTE PRECISARÁ AJUSTAR ESSES VALORES!
        posicao_nome = (180, 700)
        posicao_detalhes = (180, 800)
        
        cor_texto = (0, 0, 0) # Preto

        # 4. "Desenha" o texto na imagem
        draw.text(posicao_nome, nome, fill=cor_texto, font=font_nome)
        draw.multiline_text(posicao_detalhes, texto_resumo, fill=cor_texto, font=font_corpo, spacing=10)

        # 5. Salva a imagem em um buffer de memória (sem criar um arquivo no disco)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

    except FileNotFoundError:
        st.error(f"Erro: Verifique se os arquivos 'template_resumo.jpg' e 'font.ttf' estão na pasta do projeto.")
        return None
    except Exception as e:
        st.error(f"Ocorreu um erro ao gerar a imagem: {e}")
        return None
        
# Função para bloquear horário para um barbeiro específico
def bloquear_horario(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado. Não é possível bloquear.")
        return False

    # 1. Converte a string de data "dd/mm/yyyy" para um objeto de data.
    try:
        data_obj = datetime.strptime(data, '%d/%m/%Y')
    except ValueError:
        st.error("Formato de data inválido para bloqueio.")
        return False

    # 2. Usa o objeto de data para criar o ID no formato CORRETO (YYYY-MM-DD).
    data_para_id = data_obj.strftime('%Y-%m-%d')
    chave_bloqueio = f"{data_para_id}_{horario}_{barbeiro}_BLOQUEADO"

    try:
        # 3. Usa a chave correta para criar o documento de bloqueio.
        db.collection('agendamentos').document(chave_bloqueio).set({
            'nome': "BLOQUEADO",
            'telefone': "BLOQUEADO",
            'servicos': ["BLOQUEADO"],
            'barbeiro': barbeiro,
            'data': data_obj,  # Salva o objeto de data no documento
            'horario': horario,
            'agendado_por': 'bloqueio_interno' # Campo para identificar a origem
        })
        return True
    except Exception as e:
        st.error(f"Erro ao bloquear horário: {e}")
        return False

# Interface Streamlit
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

# Gerenciamento da Data Selecionada no Session State
if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().date()  # Inicializar como objeto date

if 'date_changed' not in st.session_state:
    st.session_state['date_changed'] = False

def handle_date_change():
    st.session_state.data_agendamento = st.session_state.data_input_widget  # Atualizar com o objeto date
    # verificar_disponibilidade.clear() # Limpar cache se estivesse usando @st.cache_data
    st.session_state['date_changed'] = True # Indica que a data mudou
    # st.rerun() # Força o rerender da página para atualizar a tabela imediatamente (opcional, mas melhora UX)

data_agendamento_obj = st.date_input(
    "Data para visualizar disponibilidade",
    value=st.session_state.data_agendamento, # Usa o valor do session state
    min_value=datetime.today().date(), # Garante que seja um objeto date
    key="data_input_widget",
    on_change=handle_date_change
)

# Atualiza o session state se o valor do widget for diferente (necessário se não usar on_change ou rerun)
if data_agendamento_obj != st.session_state.data_agendamento:
     st.session_state.data_agendamento = data_agendamento_obj
    

# Sempre usa a data do session_state para consistência
# --- Tabela de Disponibilidade ---

# SUAS LINHAS - MANTIDAS EXATAMENTE COMO PEDIU
data_para_tabela = st.session_state.data_agendamento.strftime('%d/%m/%Y')
data_obj_tabela = st.session_state.data_agendamento

st.subheader("Disponibilidade dos Barbeiros")

# 1. CHAMA A FUNÇÃO RÁPIDA UMA ÚNICA VEZ
# Usamos o objeto de data que você já tem
agendamentos_do_dia = buscar_agendamentos_e_bloqueios_do_dia(data_obj_tabela)

# 2. CRIA A VARIÁVEL COM O FORMATO CORRETO PARA O ID
# Esta é a adição importante. Usamos o objeto de data para criar a string YYYY-MM-DD
data_para_id_tabela = data_obj_tabela.strftime('%Y-%m-%d')

# --- O resto da sua lógica de construção da tabela continua, mas usando a variável correta ---
html_table = '<table style="font-size: 14px; border-collapse: collapse; width: 100%; border: 1px solid #ddd;"><tr><th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>'
for barbeiro in barbeiros:
    html_table += f'<th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white; min-width: 120px; text-align: center;">{barbeiro}</th>'
html_table += '</tr>'

dia_da_semana_tabela = data_obj_tabela.weekday()
horarios_tabela = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
dia_tabela = data_obj_tabela.day
mes_tabela = data_obj_tabela.month
intervalo_especial = mes_tabela == 7 and 10 <= dia_tabela <= 19

for horario in horarios_tabela:
    html_table += f'<tr><td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{horario}</td>'
    for barbeiro in barbeiros:
        
        if not intervalo_especial and horario in ["07:00", "07:30"]:
            status, bg_color, color_text = "SDJ", "#696969", "white"
            html_table += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status}</td>'
            continue
        # A nova regra: SÓ bloqueia as 8:00 se NÃO for o intervalo especial
        if dia_da_semana_tabela < 5 and not intervalo_especial and horario == "08:00" and barbeiro == "Lucas Borges":
            status = "Indisponível"
            bg_color = "#808080"
            color_text = "white"
            html_table += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status}</td>'
            continue

        # 3. A CORREÇÃO CRUCIAL
        # Usamos a nova variável `data_para_id_tabela` para criar a chave
        chave_agendamento = f"{data_para_id_tabela}_{horario}_{barbeiro}"
        chave_bloqueio = f"{chave_agendamento}_BLOQUEADO"
        dados_do_horario = agendamentos_do_dia.get(chave_agendamento) or agendamentos_do_dia.get(chave_bloqueio)

        if dados_do_horario:
            # Se o nome for "FECHADO", o status é final.
            if "FECHADO" in dados_do_horario.get("nome", "").upper():
                status, bg_color, color_text = "Fechado", "#6c757d", "white"
            # Se for qualquer outro agendamento, o status é Ocupado.
            else:
                status, bg_color, color_text = "Ocupado", "firebrick", "white"
        else:
            # Se não há dados, o status inicial é Disponível.
            status, bg_color, color_text = "Disponível", "forestgreen", "white"

        if status == "Disponível":
            if dia_da_semana_tabela < 5:
                hora_int = int(horario.split(':')[0])
                if not intervalo_especial and (hora_int == 12 or hora_int == 13):
                    status, bg_color, color_text = "Almoço", "orange", "black"

            if dia_da_semana_tabela == 6 and not intervalo_especial:
                 status, bg_color, color_text = "Fechado", "#A9A9A9", "black"
        
        html_table += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status}</td>'

    html_table += '</tr>'

st.markdown(html_table, unsafe_allow_html=True)

# Aba de Agendamento (FORMULÁRIO)
with st.form("agendar_form"):
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome")
    telefone = st.text_input("Telefone")

    # Usar o valor do session state para a data DENTRO do formulário
    # A data exibida aqui será a mesma da tabela, pois ambas usam session_state
    st.write(f"Data selecionada: **{st.session_state.data_agendamento.strftime('%d/%m/%Y')}**")
    data_agendamento_str_form = st.session_state.data_agendamento.strftime('%d/%m/%Y') # String para salvar
    data_obj_agendamento_form = st.session_state.data_agendamento # Objeto date para validações

    # Geração da lista de horários completa para agendamento
    horarios_base_agendamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    barbeiro_selecionado = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])

    # Filtrar horários de almoço com base no barbeiro selecionado ou "Sem preferência"
    # (Opcional: Poderia filtrar aqui, mas a validação no submit é mais robusta)
    horarios_disponiveis_dropdown = horarios_base_agendamento # Por enquanto, mostra todos
    # --- Lógica de filtragem complexa poderia entrar aqui ---
    # Mas é mais seguro validar APÓS o submit, pois a disponibilidade pode mudar

    horario_agendamento = st.selectbox("Horário", horarios_disponiveis_dropdown)

    servicos_selecionados = st.multiselect("Serviços", lista_servicos)

    # Exibir os preços com o símbolo R$
    st.write("Serviços disponíveis:")
    for servico in servicos:
        st.write(f"- {servico}")

    submitted = st.form_submit_button("Confirmar Agendamento")
    

if submitted:
    with st.spinner("Processando agendamento..."):
        # --- 1. COLETA DE DADOS ---
        nome_cliente = st.session_state.get("nome_cliente", "")
        telefone_cliente = st.session_state.get("telefone_cliente", "")
        servicos_selecionados = st.session_state.get("servicos_selecionados", [])
        data_obj_agendamento = st.session_state.agendamento_info['data_obj']
        horario_agendamento = st.session_state.agendamento_info['horario']
        barbeiro_selecionado = st.session_state.agendamento_info['barbeiro']
        data_agendamento_str = data_obj_agendamento.strftime('%d/%m/%Y')
        data_para_id = data_obj_agendamento.strftime('%Y-%m-%d')

        # --- 2. VALIDAÇÕES DE REGRAS DE NEGÓCIO (PRIORIDADE MÁXIMA) ---

        # Validação de preenchimento de campos
        if not nome_cliente or not telefone_cliente or not servicos_selecionados:
            st.error("Por favor, preencha seu nome, telefone e selecione pelo menos um serviço.")
            st.stop()

        # Validação de regras de dia/horário especiais
        dia_da_semana = data_obj_agendamento.weekday()
        mes = data_obj_agendamento.month
        dia = data_obj_agendamento.day
        intervalo_especial = (mes == 7 and 10 <= dia <= 19)

        # Regra do Domingo
        if dia_da_semana == 6 and not intervalo_especial:
            st.error("Desculpe, estamos fechados aos domingos.")
            st.stop()
        
        # Regra do horário 07:00/07:30
        if horario_agendamento in ["07:00", "07:30"] and not intervalo_especial:
            st.error("Os horários de 07:00 e 07:30 só estão disponíveis durante o período especial de Julho.")
            st.stop()
            
        # Regra do Visagismo
        servicos_visagismo = ["Abordagem de visagismo", "Consultoria de visagismo"]
        visagismo_selecionado = any(s in servicos_selecionados for s in servicos_visagismo)
        if visagismo_selecionado and barbeiro_selecionado == "Aluizio":
            st.error("Apenas Lucas Borges realiza atendimentos de visagismo.")
            st.stop()

        # --- 3. LÓGICA DE VERIFICAÇÃO DE DISPONIBILIDADE (O CORAÇÃO DO CÓDIGO) ---

        barbeiros_a_verificar = []
        if barbeiro_selecionado != "Sem preferência":
            barbeiros_a_verificar.append(barbeiro_selecionado)
        elif visagismo_selecionado:
            barbeiros_a_verificar.append("Lucas Borges")
            st.info("Serviço de visagismo selecionado. O agendamento será com Lucas Borges.")
        else:
            barbeiros_a_verificar = ["Aluizio", "Lucas Borges"]

        barbeiro_agendado = None
        for b in barbeiros_a_verificar:
            # Verificação direta e em tempo real no banco de dados
            id_documento = f"{data_para_id}_{horario_agendamento}_{b}"
            doc_ref = db.collection('agendamentos').document(id_documento)
            doc = doc_ref.get()

            if not doc.exists: # Se o documento NÃO existe, o horário está LIVRE!
                barbeiro_agendado = b
                break # Encontrou um barbeiro, para o loop.

        # Se o loop terminou e não encontrou ninguém, o horário está ocupado.
        if not barbeiro_agendado:
            st.error(f"Desculpe, o horário das {horario_agendamento} não está mais disponível. Por favor, escolha outro.")
            st.stop()

        # --- 4. VALIDAÇÃO E BLOQUEIO DO HORÁRIO SEGUINTE (se necessário) ---
        
        precisa_bloquear_proximo = False
        corte_selecionado = any(c in servicos_selecionados for c in ["Tradicional", "Social", "Degradê", "Navalhado"])
        barba_selecionada = "Barba" in servicos_selecionados

        if corte_selecionado and barba_selecionada:
            horario_seguinte_dt = datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)
            horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
            id_doc_seguinte = f"{data_para_id}_{horario_seguinte_str}_{barbeiro_agendado}"
            doc_ref_seguinte = db.collection('agendamentos').document(id_doc_seguinte)
            doc_seguinte = doc_ref_seguinte.get()

            if doc_seguinte.exists:
                st.error(f"Não é possível agendar Corte e Barba. O barbeiro {barbeiro_agendado} já está ocupado às {horario_seguinte_str}.")
                st.stop()
            else:
                precisa_bloquear_proximo = True

        # --- 5. SALVAR NO BANCO DE DADOS E FINALIZAR ---
        # Se chegamos aqui, está tudo certo para salvar.
        try:
            # Salva o agendamento principal
            user_data = {
                'nome': nome_cliente, 'telefone': telefone_cliente, 'servicos': servicos_selecionados,
                'data': data_para_id, 'horario': horario_agendamento, 'barbeiro': barbeiro_agendado,
                'timestamp': firestore.SERVER_TIMESTAMP
            }
            db.collection('agendamentos').document(f"{data_para_id}_{horario_agendamento}_{barbeiro_agendado}").set(user_data)

            # Bloqueia o horário seguinte se for Corte+Barba
            if precisa_bloquear_proximo:
                horario_seguinte_str = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                db.collection('agendamentos').document(f"{data_para_id}_{horario_seguinte_str}_{barbeiro_agendado}_BLOQUEADO").set({
                    'nome': 'Fechado', 'motivo': f'Extensão de {nome_cliente}', 'timestamp': firestore.SERVER_TIMESTAMP
                })


            # --- Preparar e Enviar E-mail ---
            resumo = f"""
            Nome: {nome_cliente}
            Telefone: {telefone_cliente}
            Data: {data_agendamento_str}
            Horário: {horario_agendamento}
            Barbeiro: {barbeiro_agendado}
            Serviços: {', '.join(servicos_selecionados)}
            """
            enviar_email("Agendamento Confirmado", resumo)

            # --- Mensagem de Sucesso e Rerun ---
            st.success("Agendamento confirmado com sucesso!")
            st.info("Resumo do agendamento:\n" + resumo)
            if precisa_bloquear_proximo:
                st.info(f"O horário das {horario_seguinte_str} com {barbeiro_agendado} foi bloqueado para acomodar todos os serviços.")
            
            # ### INÍCIO DA MODIFICAÇÃO ###
            # Chama a função para gerar a imagem com os dados do agendamento
            imagem_bytes = gerar_imagem_resumo(
                nome=nome_cliente,
                data=data_agendamento_str,
                horario=horario_agendamento,
                barbeiro=barbeiro_agendado,
                servicos=servicos_selecionados
            )

            # Se a imagem foi gerada corretamente, mostra o botão de download
            if imagem_bytes:
                st.download_button(
                    label="📥 Baixar Resumo do Agendamento",
                    data=imagem_bytes,
                    file_name=f"agendamento_{nome_cliente.split(' ')[0]}_{data_agendamento_str.replace('/', '-')}.png",
                    mime="image/png"
                )
            st.info("A página será atualizada em 15 segundos.")
            time.sleep(15) 
            st.rerun()
        except Exception as e:
            # Mensagem de erro se salvar_agendamento falhar (já exibida pela função)
            st.error("Não foi possível completar o agendamento. Verifique as mensagens de erro acima ou tente novamente.")


# Aba de Cancelamento
with st.form("cancelar_form"):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Telefone usado no Agendamento")
    data_cancelar = st.date_input("Data do Agendamento", min_value=datetime.today().date()) # Usar date()

    # Geração da lista de horários completa para cancelamento
    horarios_base_cancelamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    horario_cancelar = st.selectbox("Horário do Agendamento", horarios_base_cancelamento) # Usa a lista completa

    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento", barbeiros)
    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")

if submitted_cancelar:
    if not telefone_cancelar:
        st.error("Por favor, informe o telefone utilizado no agendamento.")
    else:
        with st.spinner("Processando cancelamento..."):
            # --- 1. PREPARAÇÃO DOS DADOS ---
            data_para_id = data_cancelar.strftime('%Y-%m-%d')
            doc_id_principal = f"{data_para_id}_{horario_cancelar}_{barbeiro_cancelar}"

            try:
                # --- 2. EXECUÇÃO DO CANCELAMENTO PRINCIPAL ---
                # Esta função busca o doc, valida o telefone e o deleta.
                # Ela retorna os dados do agendamento cancelado em caso de sucesso.
                resultado_cancelamento = cancelar_agendamento(doc_id_principal, telefone_cancelar)

                # Se a função retornou uma string, foi um erro (ex: telefone não confere)
                if isinstance(resultado_cancelamento, str):
                    st.error(resultado_cancelamento)
                    st.stop()
                
                # Se retornou um dicionário, o cancelamento principal deu certo.
                if isinstance(resultado_cancelamento, dict):
                    agendamento_cancelado = resultado_cancelamento
                    
                    # --- 3. LÓGICA PARA DESBLOQUEAR HORÁRIO SEGUINTE (SE FOR O CASO) ---
                    servicos = agendamento_cancelado.get('servicos', [])
                    corte_selecionado = any(c in servicos for c in ["Tradicional", "Social", "Degradê", "Navalhado"])
                    barba_selecionada = "Barba" in servicos

                    horario_seguinte_desbloqueado = False
                    if corte_selecionado and barba_selecionada:
                        # Calculamos o ID exato do documento de bloqueio
                        horario_original = agendamento_cancelado.get('horario')
                        horario_seguinte_str = (datetime.strptime(horario_original, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                        
                        # Usamos o mesmo padrão de ID que foi usado para criar o bloqueio
                        id_documento_bloqueado = f"{data_para_id}_{horario_seguinte_str}_{barbeiro_cancelar}_BLOQUEADO"
                        
                        # Tentamos deletar o documento de bloqueio diretamente
                        doc_ref_bloqueio = db.collection('agendamentos').document(id_documento_bloqueado)
                        doc_bloqueio = doc_ref_bloqueio.get()

                        # Apenas tentamos deletar se o bloqueio realmente existir
                        if doc_bloqueio.exists:
                            doc_ref_bloqueio.delete()
                            horario_seguinte_desbloqueado = True

                    # --- 4. MENSAGEM DE SUCESSO E NOTIFICAÇÃO ---
                    # (Sua lógica de e-mail e sucesso, sem alterações)
                    resumo_cancelamento = f"""
                    Agendamento Cancelado:
                    Nome: {agendamento_cancelado.get('nome', 'N/A')}
                    Telefone: {agendamento_cancelado.get('telefone', 'N/A')}
                    Data: {data_cancelar.strftime('%d/%m/%Y')}
                    Horário: {agendamento_cancelado.get('horario', 'N/A')}
                    Barbeiro: {agendamento_cancelado.get('barbeiro', 'N/A')}
                    Serviços: {', '.join(agendamento_cancelado.get('servicos', []))}
                    """
                    enviar_email("Agendamento Cancelado", resumo_cancelamento)
            
                    st.success("Agendamento cancelado com sucesso!")
                    if horario_seguinte_desbloqueado:
                        st.info("O horário seguinte, que estava bloqueado para Corte+Barba, também foi liberado.")
            
                    time.sleep(5)
                    st.rerun()

                


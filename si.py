import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
import google.api_core.retry as retry


# Carregar as credenciais do Firebase e e-mail a partir do Streamlit secrets
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
horarios = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
servicos = {
    "Tradicional": 15,
    "Social": 18,
    "Degradê": 23,
    "Navalhado": 25,
    "Pezim": 5,
    "Barba": 15,
}

barbeiros = ["Lucas Borges", "Aluizio", "Sem preferência"]

# Função para enviar e-mail
def enviar_email(assunto, mensagem):
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

# Função para salvar agendamento no Firestore
def salvar_agendamento(data, horario, nome, telefone, servicos, barbeiro):
    chave_agendamento = f"{data}_{horario}"
    db.collection('agendamentos').document(chave_agendamento).set({
        'nome': nome,
        'telefone': telefone,
        'servicos': servicos,
        'barbeiro': barbeiro,
        'data': data,
        'horario': horario
    })

# Função para cancelar agendamento no Firestore
def cancelar_agendamento(data, horario, telefone):
    chave_agendamento = f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_ref.delete()
            return doc.to_dict()  # Retorna os dados do agendamento cancelado
        else:
            return None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore: {e}")
        return None
def obter_disponibilidade(data):
    disponibilidade = {barbeiro: {hora: "verde" for hora in horarios} for barbeiro in barbeiros}
    agendamentos = db.collection("agendamentos").where("data", "==", data).stream()

    for agendamento in agendamentos:
        info = agendamento.to_dict()
        barbeiro = info["barbeiro"]
        horario = info["horario"]
        disponibilidade[barbeiro][horario] = "vermelho"

    for horario in horarios:
        # Verifica se TODOS os barbeiros estão ocupados no horário
        barbeiros_ocupados = [b for b in barbeiros if disponibilidade[b][horario] == "vermelho" and b != "Sem preferência"]
        
        if len(barbeiros_ocupados) == len(barbeiros) - 1:  # Todos, exceto "Sem preferência"
            disponibilidade["Sem preferência"][horario] = "vermelho"
        elif len(barbeiros_ocupados) > 0:  # Pelo menos um barbeiro ocupado
            disponibilidade["Sem preferência"][horario] = "amarelo"

    return disponibilidade


# Função para verificar disponibilidade do horário no Firebase
@retry.Retry()
def verificar_disponibilidade(data, horario):
    if not db:
        st.error("Firestore não inicializado.")
        return False  # Retorna False se o Firestore não estiver inicializado
    chave_agendamento = f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists:
            st.write(f"Horário {horario} no dia {data} já ocupado.")
        else:
            st.write(f"Horário {horario} no dia {data} disponível.")
        return not doc.exists  # Retorna True se o horário estiver disponível
    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore: {e}")
        return False  # Retorna False em caso de erro
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return False  # Retorna False em caso de erro
if "disponibilidade" not in st.session_state:
    data_hoje = datetime.today().strftime('%d/%m/%Y')
    st.session_state.disponibilidade = obter_disponibilidade(data_hoje)

# Interface Streamlit
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

# Aba de Agendamento
st.subheader("Agendar Horário")
nome = st.text_input("Nome")
telefone = st.text_input("Telefone")
data = st.date_input("Data", min_value=datetime.today()).strftime('%d/%m/%Y')
horario = st.selectbox("Horário", horarios)
barbeiro = st.selectbox("Escolha o barbeiro", barbeiros)
servicos_selecionados = st.multiselect("Serviços", list(servicos.keys()))
st.subheader("Horários disponíveis")

for horario in horarios:
    cols = st.columns(len(barbeiros))
    for i, barbeiro in enumerate(barbeiros):
        cor = st.session_state.disponibilidade[barbeiro][horario]
        bolinha = f"🔴" if cor == "vermelho" else f"🟡" if cor == "amarelo" else f"🟢"
        cols[i].markdown(f"{bolinha} {barbeiro} - {horario}")

# Exibir os preços com o símbolo R$
servicos_com_preco = {servico: f"R$ {preco}" for servico, preco in servicos.items()}
st.write("Preços dos serviços:")
for servico, preco in servicos_com_preco.items():
    st.write(f"{servico}: {preco}")

# Validação dos serviços selecionados
if st.button("Confirmar Agendamento"):
    if nome and telefone and servicos_selecionados:
        if "Sem preferência" in barbeiro:
            barbeiro = "Sem preferência"

        if len(servicos_selecionados) > 2:
            st.error("Você pode agendar no máximo 2 serviços, sendo o segundo sempre a barba.")
        elif len(servicos_selecionados) == 2 and "Barba" not in servicos_selecionados:
            st.error("Se você escolher dois serviços, o segundo deve ser a barba.")
        else:
            with st.spinner("Verificando disponibilidade..."):
                if verificar_disponibilidade(data, horario):
                    resumo = f"""
                    Nome: {nome}
                    Telefone: {telefone}
                    Data: {data}
                    Horário: {horario}
                    Barbeiro: {barbeiro}
                    Serviços: {', '.join(servicos_selecionados)}
                    """
                    salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)
                    
                    # Atualiza disponibilidade, mas sem recarregar a página
                    st.session_state.disponibilidade = obter_disponibilidade(data)
                    
                    st.success("Agendamento confirmado com sucesso!")
                    st.info("Resumo do agendamento:\n" + resumo)
                else:
                    st.error("O horário escolhido já está ocupado. Por favor, selecione outro horário.")
    else:
        st.error("Por favor, preencha todos os campos e selecione pelo menos 1 serviço.")


# Aba de Cancelamento
st.subheader("Cancelar Agendamento")
telefone_cancelar = st.text_input("Telefone para Cancelamento")
horario_cancelar = st.selectbox("Horário do Agendamento", horarios)

if st.button("Cancelar Agendamento"):
    with st.spinner("Processando cancelamento..."):
        cancelado = cancelar_agendamento(data, horario_cancelar, telefone_cancelar)
        if cancelado:
            resumo_cancelamento = f"""
            Nome: {cancelado['nome']}
            Telefone: {cancelado['telefone']}
            Data: {cancelado['data']}
            Horário: {cancelado['horario']}
            Barbeiro: {cancelado['barbeiro']}
            Serviços: {', '.join(cancelado['servicos'])}
            """
            enviar_email("Agendamento Cancelado", resumo_cancelamento)

            # Atualiza disponibilidade sem recarregar a página
            st.session_state.disponibilidade = obter_disponibilidade(data)

            st.success("Agendamento cancelado com sucesso!")
            st.info("Resumo do cancelamento:\n" + resumo_cancelamento)
        else:
            st.error("Não há agendamento para o telefone informado nesse horário.")


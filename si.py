import streamlit as st 
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
import google.api_core.retry as retry
import random

# Carregar as credenciais do Firebase e e-mail a partir do Streamlit secrets
FIREBASE_CREDENTIALS = None
EMAIL = None
SENHA = None

try:
    firebase_credentials_json = st.secrets["firebase"]["FIREBASE_CREDENTIALS"]
    FIREBASE_CREDENTIALS = json.loads(firebase_credentials_json)
    EMAIL = st.secrets["email"]["EMAIL_CREDENCIADO"]
    SENHA = st.secrets["email"]["EMAIL_SENHA"]
except KeyError as e:
    st.error(f"Chave ausente no arquivo secrets.toml: {e}")
except json.JSONDecodeError as e:
    st.error(f"Erro ao decodificar as credenciais do Firebase: {e}")
except Exception as e:
    st.error(f"Erro inesperado: {e}")

# Inicializar Firebase
if FIREBASE_CREDENTIALS:
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"Erro ao inicializar o Firebase: {e}")

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
            server.login(EMAIL, SENHA)
            server.sendmail(EMAIL, EMAIL, msg.as_string())
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")

# Salvar agendamento no Firestore
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

# Cancelar agendamento
def cancelar_agendamento(data, horario, telefone):
    chave_agendamento = f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_info = doc.to_dict()
            agendamento_ref.delete()

            # Recalcular os status dos horários após o cancelamento
            status_horarios_atualizado = obter_status_horarios(data)

            return agendamento_info, status_horarios_atualizado  # Retorna os dados cancelados e os novos status
        else:
            return None, None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore: {e}")
        return None, None

# Verificar status dos horários com bloqueio de 2 serviços
import random

def obter_status_horarios(data, barbeiro=None):
    horarios_status = {h: "disponivel" for h in horarios}
    ocupacoes = {h: [] for h in horarios}
    bloqueios_extra = {}

    try:
        docs = db.collection('agendamentos').where('data', '==', data).stream()

        for doc in docs:
            agendamento = doc.to_dict()
            h = agendamento['horario']
            b = agendamento['barbeiro']
            
            ocupacoes[h].append(b)

            # Se for corte + barba, bloqueia o próximo horário
            if len(agendamento['servicos']) == 2 and "Barba" in agendamento['servicos']:
                idx = horarios.index(h)
                if idx + 1 < len(horarios):
                    h_next = horarios[idx + 1]
                    if h_next not in bloqueios_extra:
                        bloqueios_extra[h_next] = []
                    bloqueios_extra[h_next].append(b)

        for h in horarios:
            barbeiros_ocupados = ocupacoes[h]

            if len(barbeiros_ocupados) == len(barbeiros):  
                horarios_status[h] = "ocupado"
            elif len(barbeiros_ocupados) > 0:
                horarios_status[h] = "parcial"

        for h, bloqueados in bloqueios_extra.items():
            if len(bloqueados) == len(barbeiros):
                horarios_status[h] = "ocupado"
            else:
                horarios_status[h] = "parcial"

        # Se barbeiro não foi informado, escolher um disponível
        if barbeiro is None:
            agendamentos_distribuidos = {}
            for h, status in horarios_status.items():
                if status == "disponivel" or status == "parcial":
                    barbeiros_livres = [b for b in barbeiros if b not in ocupacoes[h]]
                    
                    if barbeiros_livres:
                        barbeiro_escolhido = random.choice(barbeiros_livres)
                    else:
                        barbeiro_escolhido = None  # Todos ocupados, informar usuário

                    agendamentos_distribuidos[h] = barbeiro_escolhido

            return horarios_status, agendamentos_distribuidos

        return horarios_status

    except Exception as e:
        print(f"Erro ao obter status dos horários: {e}")
        return {}, {}

        # Bloquear horário entre 12h e 14h (exceto sábado)
        dia_semana = datetime.strptime(data, '%d/%m/%Y').weekday()
        if dia_semana < 5:  # Segunda a sexta
            for h in horarios:
                hora_int = int(h[:2])
                if 12 <= hora_int < 14:
                    horarios_status[h] = "bloqueado"

    except Exception as e:
        st.error(f"Erro ao buscar agendamentos: {e}")

    return horarios_status

# INTERFACE STREAMLIT
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/agendonline---barbearialb/blob/main/icone.png?raw=true", use_container_width=True)

# Aba Agendamento
st.subheader("Agendar Horário")
nome = st.text_input("Nome")
telefone = st.text_input("Telefone")
data_obj = st.date_input("Data", min_value=datetime.today())
data = data_obj.strftime('%d/%m/%Y')
barbeiro = st.selectbox("Escolha o barbeiro", barbeiros)
status_horarios, agendamentos_distribuidos = obter_status_horarios(data, barbeiro)


# Mostrar horários com bolinhas coloridas
status_horarios = obter_status_horarios(data)
horarios_disponiveis_para_selecao = []
horarios_coloridos = []

for h in horarios:
    status = status_horarios[h]
    if status == "ocupado":
        horarios_coloridos.append(f"🔴 {h}")
    elif status == "parcial":
        horarios_coloridos.append(f"🟡 {h}")
        horarios_disponiveis_para_selecao.append(h)
    else:
        horarios_coloridos.append(f"🟢 {h}")
        horarios_disponiveis_para_selecao.append(h)

horario_index = st.selectbox("Horário", list(range(len(horarios_coloridos))),
                             format_func=lambda x: horarios_coloridos[x])
horario = horarios[horario_index]

def escolher_barbeiro(data, horario):
    """Escolhe um barbeiro disponível ou aleatoriamente se ambos estiverem livres."""
    docs = db.collection('agendamentos').where('data', '==', data).where('horario', '==', horario).stream()

    barbeiros_ocupados = {doc.to_dict()['barbeiro'] for doc in docs}
    barbeiros_disponiveis = [b for b in barbeiros if b != "Sem preferência" and b not in barbeiros_ocupados]

    if len(barbeiros_disponiveis) == 1:
        return barbeiros_disponiveis[0]  # Se um está livre, escolher automaticamente
    elif len(barbeiros_disponiveis) == 2:
        return random.choice(barbeiros_disponiveis)  # Se ambos estão livres, escolher aleatoriamente
    else:
        return None  # Se ambos estão ocupados

# Exibir preços
servicos_com_preco = {s: f"R$ {p}" for s, p in servicos.items()}
st.write("Preços dos serviços:")
for s, p in servicos_com_preco.items():
    st.write(f"{s}: {p}")

# Seleção de serviços
servicos_selecionados = st.multiselect("Serviços", list(servicos.keys()))

# Validação e Agendamento
if st.button("Confirmar Agendamento"):
    if nome and telefone and servicos_selecionados:
        if len(servicos_selecionados) > 2:
            st.error("Você pode agendar no máximo 2 serviços, sendo o segundo sempre a barba.")
        elif len(servicos_selecionados) == 2 and "Barba" not in servicos_selecionados:
            st.error("Se você escolher dois serviços, o segundo deve ser a barba.")
        elif status_horarios[horario] == "ocupado" or status_horarios[horario] == "bloqueado":
            st.error("O horário está ocupado. Escolha outro.")
        else:
            # Se "Sem preferência" for escolhido, definir automaticamente o barbeiro
            if barbeiro == "Sem preferência":
                barbeiro = escolher_barbeiro(data, horario)
                if not barbeiro:
                    st.error("Não há barbeiros disponíveis para este horário. Escolha outro.")
                    st.stop()

            resumo = f"""
            Nome: {nome}
            Telefone: {telefone}
            Data: {data}
            Horário: {horario}
            Barbeiro: {barbeiro}
            Serviços: {', '.join(servicos_selecionados)}
            """

            salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)
            enviar_email("Agendamento Confirmado", resumo)
            st.success("Agendamento confirmado com sucesso!")
            st.info("Resumo do agendamento:\n" + resumo)
    else:
        st.error("Preencha todos os campos e selecione pelo menos 1 serviço.")
# Aba Cancelamento
st.subheader("Cancelar Agendamento")

telefone_cancelar = st.text_input("Telefone para Cancelamento")
horario_cancelar = st.selectbox("Horário do Agendamento", horarios)

if st.button("Cancelar Agendamento"):
    cancelado, status_horarios_atualizado = cancelar_agendamento(data, horario_cancelar, telefone_cancelar)

    if cancelado:
        resumo_cancel = f"""
        Nome: {cancelado['nome']}
        Telefone: {cancelado['telefone']}
        Data: {cancelado['data']}
        Horário: {cancelado['horario']}
        Barbeiro: {cancelado['barbeiro']}
        Serviços: {', '.join(cancelado['servicos'])}
        """

        enviar_email("Agendamento Cancelado", resumo_cancel)
        st.success("Agendamento cancelado com sucesso!")
        st.info("Resumo do cancelamento:\n" + resumo_cancel)

        # ✅ Atualiza as cores dos horários
        horarios_coloridos = []
        for h in horarios:
            status = status_horarios_atualizado[h]
            if status == "ocupado":
                horarios_coloridos.append(f"🔴 {h}")
            elif status == "parcial":
                horarios_coloridos.append(f"🟡 {h}")
            else:
                horarios_coloridos.append(f"🟢 {h}")

        # Atualiza o selectbox de horários após cancelamento
        horario_index = st.selectbox("Horário", list(range(len(horarios_coloridos))),
                                     format_func=lambda x: horarios_coloridos[x])

    else:
        st.error("Não há agendamento com esse telefone nesse horário.")

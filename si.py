# -*- coding: utf-8 -*-
import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
# Removido retry não utilizado diretamente nas funções principais agora
# import google.api_core.retry as retry
import random
import pandas as pd
import time
import uuid # <<< ADICIONADO para IDs únicos

# --- Configuração Inicial e Constantes ---

st.markdown(
"""
<style>
table {
    display: block !important;
    width: fit-content !important; /* Ou tente width: -webkit-fill-available !important; */
}
div[data-testid="stForm"] {
    display: block !important;
}
</style>
""",
    unsafe_allow_html=True,
)

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
    st.error(f"Erro inesperado ao carregar secrets: {e}")

# Inicializar Firebase com as credenciais
db = None
if FIREBASE_CREDENTIALS:
    if not firebase_admin._apps: # Verifica se o Firebase já foi inicializado
        try:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
        except Exception as e:
            st.error(f"Erro ao inicializar o Firebase: {e}")
else:
    st.error("Credenciais do Firebase não carregadas. Verifique os secrets.")

# Dados básicos
servicos = {
    "Tradicional": 15,
    "Social": 18,
    "Degradê": 23,
    "Pezim": 7,
    "Navalhado": 25,
    "Barba": 15,
    "Abordagem de visagismo": 45,
    "Consultoria de visagismo": 65,
}
lista_servicos = list(servicos.keys())
barbeiros = ["Lucas Borges", "Aluizio"]

#--- Constantes para Estados e Cores ---
ESTADO_DISPONIVEL = "Disponível"
ESTADO_OCUPADO = "Ocupado"
ESTADO_PEZIM_AGENDADO = "Pezim (Rápido)" # Estado onde SÓ pezim está agendado, permite adicionar outros
ESTADO_INDISPONIVEL = "Indisponível" # Para almoço/bloqueio manual ou bloqueio de combo

COR_DISPONIVEL = "forestgreen"
COR_OCUPADO = "firebrick"
COR_PEZIM = "darkblue"       # Cor para ESTADO_PEZIM_AGENDADO
COR_INDISPONIVEL = "orange"  # Cor para ESTADO_INDISPONIVEL

#--- Listas de Serviços para Regras ---
PEZIM = "Pezim"
SERVICOS_PERMITIDOS_COM_PEZIM = ["Pezim", "Tradicional", "Barba", "Social"] # O que pode ser adicionado a um slot com Pezim
SERVICOS_BLOQUEADOS_COM_PEZIM = ["Degradê", "Navalhado", "Abordagem de visagismo", "Consultoria de visagismo"] # O que NÃO pode
SERVICOS_QUE_BLOQUEIAM_HORARIO_SEGUINTE = ["Tradicional", "Social", "Degradê", "Navalhado"] # Cortes que com Barba bloqueiam

# --- Funções Auxiliares (Email, Bloqueio/Desbloqueio) ---

def enviar_email(assunto, mensagem):
    if not EMAIL or not SENHA:
        st.warning("Credenciais de e-mail não configuradas. E-mail não enviado.")
        return
    try:
        msg = MIMEText(mensagem)
        msg['Subject'] = assunto
        msg['From'] = EMAIL
        msg['To'] = EMAIL # Enviar para o próprio e-mail da barbearia
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL, SENHA)
            server.sendmail(EMAIL, EMAIL, msg.as_string())
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")

# Função para bloquear horário seguinte (NÃO PRECISA ALTERAR)
def bloquear_horario_seguinte(data, horario_atual, barbeiro):
    if not db: return False
    try:
        horario_atual_dt = datetime.strptime(horario_atual, '%H:%M')
        horario_seguinte_dt = horario_atual_dt + timedelta(minutes=30)
        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
        data_obj = datetime.strptime(data, '%d/%m/%Y')

        chave_bloqueio = f"{data}_{horario_seguinte_str}_{barbeiro}_BLOQUEADO"
        db.collection('agendamentos').document(chave_bloqueio).set({
            'nome': "BLOQUEADO",
            'telefone': "BLOQUEADO",
            'servicos': ["BLOQUEADO"],
            'barbeiro': barbeiro,
            'data': data_obj,
            'horario': horario_seguinte_str,
            'status_geral': ESTADO_INDISPONIVEL # Usar o novo nome do campo de status
        })
        # st.info(f"O horário das {horario_seguinte_str} de {barbeiro} foi bloqueado para combo.") # Info dada depois
        return True
    except ValueError:
        st.error(f"Formato de data/horário inválido ao tentar bloquear: {data} / {horario_atual}")
        return False
    except Exception as e:
        st.error(f"Erro ao bloquear horário seguinte: {e}")
        return False

# Função para desbloquear horário seguinte (NÃO PRECISA ALTERAR)
def desbloquear_horario(data, horario, barbeiro):
    if not db: return
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    agendamento_ref = db.collection('agendamentos').document(chave_bloqueio)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict().get('nome') == "BLOQUEADO":
            print(f"Tentando excluir a chave de bloqueio: {chave_bloqueio}") # Log no console
            agendamento_ref.delete()
            print(f"Horário {horario} do barbeiro {barbeiro} na data {data} desbloqueado (removido bloqueio).")
    except Exception as e:
        st.error(f"Erro ao desbloquear horário {horario}: {e}")


# --- Funções Principais de Agendamento (MODIFICADAS) ---

# MODIFICADO: Função para obter o estado detalhado e os dados do horário
@st.cache_data(ttl=10) # Cache curto para refletir mudanças mais rápido, mas evitar leituras excessivas
def obter_estado_horario(data_str, horario, barbeiro):
    """
    Verifica o estado de um horário específico no Firestore e retorna dados.
    Retorna:
        str: Um dos estados: ESTADO_DISPONIVEL, ESTADO_OCUPADO,
             ESTADO_PEZIM_AGENDADO, ESTADO_INDISPONIVEL.
        dict: Os dados COMPLETOS do documento do horário, se houver. Retorna None se disponível/erro.
    """
    if not db:
        st.error("Firestore não inicializado.")
        return ESTADO_INDISPONIVEL, None

    chave_agendamento = f"{data_str}_{horario}_{barbeiro}"
    chave_bloqueio = f"{data_str}_{horario}_{barbeiro}_BLOQUEADO"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)

    try:
        # 1. Verificar bloqueio explícito primeiro
        doc_bloqueio = bloqueio_ref.get()
        if doc_bloqueio.exists:
            return ESTADO_INDISPONIVEL, doc_bloqueio.to_dict() # Retorna dados do bloqueio

        # 2. Verificar agendamento normal
        doc_agendamento = agendamento_ref.get()
        if doc_agendamento.exists:
            dados_agendamento = doc_agendamento.to_dict()
            status_geral = dados_agendamento.get('status_geral', ESTADO_OCUPADO) # Default Ocupado se faltar status
            return status_geral, dados_agendamento # Retorna o status salvo e TODOS os dados
        else:
            # Nenhuma chave existe, horário está disponível
            return ESTADO_DISPONIVEL, None

    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore ao verificar {horario}: {e}")
        return ESTADO_INDISPONIVEL, None
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade de {horario}: {e}")
        return ESTADO_INDISPONIVEL, None

# MODIFICADO: Função transacional para salvar ou atualizar agendamento
def salvar_ou_atualizar_agendamento(data_str, horario, nome, telefone, servicos_novos, barbeiro):
    """Salva um novo agendamento ou adiciona a um existente (caso de Pezim). Usa transação."""
    if not db:
        st.error("Firestore não inicializado.")
        return None
    chave_agendamento = f"{data_str}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    data_obj = datetime.strptime(data_str, '%d/%m/%Y') # Converter string para datetime para salvar

    transaction = db.transaction()

    @firestore.transactional
    def processar_agendamento_transacional(transaction):
        doc_snapshot = agendamento_ref.get(transaction=transaction)
        dados_atuais = doc_snapshot.to_dict() if doc_snapshot.exists else None

        novo_agendamento_info = {
            'cliente_nome': nome,
            'cliente_telefone': telefone,
            'servicos': servicos_novos,
            'id_agendamento': str(uuid.uuid4()) # ID único para este agendamento
        }

        if not dados_atuais: # Horário estava DISPONÍVEL
            status_inicial = ESTADO_PEZIM_AGENDADO if len(servicos_novos) == 1 and servicos_novos[0] == PEZIM else ESTADO_OCUPADO
            dados_para_salvar = {
                'data': data_obj,
                'horario': horario,
                'barbeiro': barbeiro,
                'status_geral': status_inicial,
                'agendamentos_individuais': [novo_agendamento_info] # Lista com o primeiro agendamento
            }
            transaction.set(agendamento_ref, dados_para_salvar)
            return novo_agendamento_info # Retorna dados do agendamento criado

        else: # Horário NÃO estava disponível, verificar estado atual
            status_atual = dados_atuais.get('status_geral')
            agendamentos_existentes = dados_atuais.get('agendamentos_individuais', [])

            if status_atual == ESTADO_PEZIM_AGENDADO:
                # Pode adicionar outros serviços permitidos
                if any(s in SERVICOS_BLOQUEADOS_COM_PEZIM for s in servicos_novos):
                    raise ValueError(f"Com {PEZIM} já agendado, só pode adicionar: {', '.join(SERVICOS_PERMITIDOS_COM_PEZIM)}")

                agendamentos_existentes.append(novo_agendamento_info) # Adiciona novo agendamento à lista
                dados_para_atualizar = {
                    'agendamentos_individuais': agendamentos_existentes,
                    'status_geral': ESTADO_OCUPADO # Mudar status para ocupado
                }
                transaction.update(agendamento_ref, dados_para_atualizar)
                return novo_agendamento_info # Retorna dados do agendamento adicionado

            elif status_atual == ESTADO_OCUPADO:
                raise ValueError("Ops! Este horário já está ocupado e não permite adicionar mais serviços.")
            elif status_atual == ESTADO_INDISPONIVEL:
                 raise ValueError("Este horário está indisponível (almoço, bloqueio ou combo).")
            else:
                raise ValueError("Estado desconhecido ou inválido no Firestore.")

    try:
        resultado = processar_agendamento_transacional(transaction)
        if resultado:
            obter_estado_horario.clear() # Limpar cache após sucesso
        return resultado # Retorna dados do agendamento adicionado/criado ou None/Exception
    except ValueError as e:
        st.error(f"{e}")
        return None
    except Exception as e:
        st.error(f"Erro inesperado ao salvar/atualizar agendamento: {e}")
        return None


# MODIFICADO: Função transacional para cancelar agendamento
@firestore.transactional
def cancelar_agendamento_transacional(transaction, agendamento_ref, telefone_cancelar):
    """Processa o cancelamento dentro de uma transação."""
    doc_snapshot = agendamento_ref.get(transaction=transaction)
    if not doc_snapshot.exists:
        return None, "Documento não encontrado." # Nenhum agendamento neste horário/barbeiro

    dados_atuais = doc_snapshot.to_dict()
    agendamentos_originais = dados_atuais.get('agendamentos_individuais', [])
    agendamentos_cancelados = []
    agendamentos_restantes = []

    for agendamento in agendamentos_originais:
        if agendamento.get('cliente_telefone') == telefone_cancelar:
            agendamentos_cancelados.append(agendamento)
        else:
            agendamentos_restantes.append(agendamento)

    if not agendamentos_cancelados:
        return None, "Nenhum agendamento encontrado para este telefone neste horário."

    # Processar o resultado
    if not agendamentos_restantes:
        # Foi(foram) cancelado(s) o(s) último(s) agendamento(s) no horário
        transaction.delete(agendamento_ref)
    else:
        # Ainda há agendamentos, atualizar a lista e o status
        servicos_restantes_flat = [s for ag in agendamentos_restantes for s in ag.get('servicos', [])]
        novo_status = ESTADO_OCUPADO
        # Verifica se SÓ sobrou UM agendamento e esse agendamento é SÓ Pezim
        if len(agendamentos_restantes) == 1 and len(servicos_restantes_flat) == 1 and servicos_restantes_flat[0] == PEZIM:
             novo_status = ESTADO_PEZIM_AGENDADO

        transaction.update(agendamento_ref, {
            'agendamentos_individuais': agendamentos_restantes,
            'status_geral': novo_status
        })

    # Retorna a lista de agendamentos que foram cancelados
    return agendamentos_cancelados, None # Sucesso, retorna lista de cancelados


def executar_cancelamento(data_str, horario, telefone_cancelar, barbeiro):
    """Função principal que chama a transação de cancelamento."""
    if not db:
        st.error("Firestore não inicializado.")
        return None, "Erro de conexão."
    chave_agendamento = f"{data_str}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    try:
        transaction = db.transaction()
        agendamentos_cancelados, erro_msg = cancelar_agendamento_transacional(transaction, agendamento_ref, telefone_cancelar)

        if agendamentos_cancelados:
            obter_estado_horario.clear() # Limpar cache após sucesso
            # Formatar data para exibição (pega do primeiro cancelado, se houver)
            data_obj_firestore = dados_atuais.get('data') # Pega a data salva no doc
            if isinstance(data_obj_firestore, datetime):
                 data_formatada = data_obj_firestore.strftime('%d/%m/%Y')
            else:
                 data_formatada = data_str # Fallback para a data original

            # Adiciona a data formatada e outros campos fixos aos detalhes retornados
            for ag in agendamentos_cancelados:
                ag['data_formatada'] = data_formatada
                ag['horario'] = horario
                ag['barbeiro'] = barbeiro

            return agendamentos_cancelados, None # Retorna lista de cancelados
        else:
            return None, erro_msg # Retorna None e a mensagem de erro da transação

    except Exception as e:
        st.error(f"Erro inesperado durante cancelamento: {e}")
        return None, f"Erro inesperado: {e}"


# Função para verificar disponibilidade do horário seguinte (Usa obter_estado_horario modificada)
def verificar_disponibilidade_horario_seguinte(data_str, horario, barbeiro):
    if not db: return False
    try:
        horario_dt = datetime.strptime(horario, '%H:%M')
        horario_seguinte_dt = horario_dt + timedelta(minutes=30)
        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')

        # Usa a função obter_estado_horario MODIFICADA
        estado_seguinte, _ = obter_estado_horario(data_str, horario_seguinte_str, barbeiro)

        # Permite combo APENAS se o próximo horário estiver totalmente DISPONÍVEL
        return estado_seguinte == ESTADO_DISPONIVEL
    except ValueError:
        st.error(f"Formato de horário inválido ao verificar seguinte: {horario}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar horário seguinte: {e}")
        return False


# --- Interface Streamlit ---

st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

# Gerenciamento da data selecionada
if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().date() # Objeto date

def handle_date_change():
    # Atualizar o session_state com o novo objeto date selecionado no widget
    if "data_input_widget" in st.session_state:
         st.session_state.data_agendamento = st.session_state.data_input_widget
    obter_estado_horario.clear() # Limpa o cache ao mudar a data

data_selecionada_obj = st.date_input(
    "Data para visualizar disponibilidade",
    value=st.session_state.data_agendamento, # Usa o valor do session_state
    min_value=datetime.today().date(), # Min value como date
    key="data_input_widget", # Chave para o widget
    on_change=handle_date_change # Callback para atualizar o state e limpar cache
)

# Usa a data do session_state (que é um objeto date) e formata para string dd/mm/yyyy
data_para_tabela = st.session_state.data_agendamento.strftime('%d/%m/%Y')
dia_da_semana_tabela = st.session_state.data_agendamento.weekday() # 0=Segunda

# --- Tabela de Disponibilidade ---
st.subheader(f"Disponibilidade para {data_para_tabela}")

# Gerar HTML da tabela (usando obter_estado_horario MODIFICADA)
html_table = '<table style="font-size: 14px; border-collapse: collapse; width: 100%; border: 1px solid #ddd;"><tr><th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>'
for barbeiro in barbeiros:
    html_table += f'<th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">{barbeiro}</th>'
html_table += '</tr>'

horarios_tabela = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
table_placeholder = st.empty()
table_placeholder.markdown("Carregando disponibilidade...")

try:
    linhas_html = ""
    for horario in horarios_tabela:
        linhas_html += f'<tr><td style="padding: 8px; border: 1px solid #ddd;">{horario}</td>'
        for barbeiro in barbeiros:
            status_texto = ""
            bg_color = ""
            color_text = "white"

            hora_int = int(horario.split(':')[0])

            # --- Lógica de Horário de Almoço (PRECEDE a verificação do Firestore) ---
            is_almoco = False
            # (Segunda a Sexta)
            if dia_da_semana_tabela < 5:
                if barbeiro == "Aluizio" and hora_int == 11: is_almoco = True
                elif barbeiro == "Lucas Borges" and hora_int == 13: is_almoco = True
                elif hora_int == 12: is_almoco = True # Ambos às 12h

            if is_almoco:
                status_texto = "Indisponível (Almoço)"
                bg_color = COR_INDISPONIVEL
            else:
                # Se não for almoço, verifica o estado do Firestore
                estado_horario, _ = obter_estado_horario(data_para_tabela, horario, barbeiro) # Ignora os dados aqui

                if estado_horario == ESTADO_DISPONIVEL:
                    status_texto = "Disponível"
                    bg_color = COR_DISPONIVEL
                elif estado_horario == ESTADO_PEZIM_AGENDADO:
                    status_texto = "Pezim (Rápido)" # Permite adicionar mais
                    bg_color = COR_PEZIM
                elif estado_horario == ESTADO_OCUPADO:
                    status_texto = "Ocupado"
                    bg_color = COR_OCUPADO
                elif estado_horario == ESTADO_INDISPONIVEL:
                    status_texto = "Indisponível" # Bloqueio ou Combo
                    bg_color = COR_INDISPONIVEL
                else: # Fallback
                    status_texto = "Erro"
                    bg_color = "gray"

            linhas_html += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status_texto}</td>'
        linhas_html += '</tr>'

    html_table += linhas_html
    html_table += '</table>'
    table_placeholder.markdown(html_table, unsafe_allow_html=True)

except Exception as e:
    st.error(f"Erro ao gerar a tabela de disponibilidade: {e}")
    table_placeholder.markdown("Erro ao carregar disponibilidade.")


# --- Formulário de Agendamento ---
with st.form("agendar_form"):
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome")
    telefone = st.text_input("Telefone (use o mesmo para cancelar)")

    # Usa a data selecionada acima (já formatada)
    st.write(f"Data selecionada: **{data_para_tabela}**")
    data_agendamento_str = data_para_tabela # Usar a string dd/mm/yyyy consistentemente

    # Geração da lista de horários completa para agendamento
    horarios_base_agendamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
    barbeiro_selecionado = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])
    horario_agendamento = st.selectbox("Horário", horarios_base_agendamento)
    servicos_selecionados = st.multiselect("Serviços", lista_servicos)

    # Exibir preços
    servicos_com_preco = {servico: f"R$ {preco}" for servico, preco in servicos.items()}
    with st.expander("Ver Preços"):
        for servico, preco in servicos_com_preco.items():
            st.write(f"- {servico}: {preco}")

    submitted = st.form_submit_button("Confirmar Agendamento")

    # --- Lógica de Processamento do Agendamento (após submit) ---
    if submitted:
        with st.spinner("Processando agendamento..."):
            # 1. Validação Inicial
            if not (nome and telefone and servicos_selecionados):
                st.error("Por favor, preencha nome, telefone e selecione pelo menos 1 serviço.")
                st.stop()

            # 2. Informações de Data/Hora
            try:
                data_obj_agendamento = datetime.strptime(data_agendamento_str, '%d/%m/%Y')
                horario_obj_agendamento = datetime.strptime(horario_agendamento, '%H:%M')
                dia_da_semana_agendamento = data_obj_agendamento.weekday()
                hora_agendamento_int = horario_obj_agendamento.hour
            except ValueError:
                st.error("Formato de data ou horário inválido.")
                st.stop()

            # 3. Informações dos Serviços
            is_visagismo_selecionado = any(s in servicos_selecionados for s in ["Abordagem de visagismo", "Consultoria de visagismo"])
            is_combo_corte_barba = "Barba" in servicos_selecionados and any(c in servicos_selecionados for c in SERVICOS_QUE_BLOQUEIAM_HORARIO_SEGUINTE)

            # 4. Função Auxiliar para Almoço (Repetida aqui para clareza no fluxo)
            def is_horario_almoco(dia_semana, hora_int, barbeiro):
                if dia_semana >= 5: return False # Sáb/Dom sem regra fixa aqui
                if hora_int == 12: return True
                if barbeiro == "Aluizio" and hora_int == 11: return True
                if barbeiro == "Lucas Borges" and hora_int == 13: return True
                return False

            # 5. Determinar Barbeiro Final e Validar Disponibilidade
            barbeiro_final = None
            barbeiros_a_checar = []

            if barbeiro_selecionado == "Sem preferência":
                barbeiros_a_checar = barbeiros # Checar ambos
            else:
                barbeiros_a_checar = [barbeiro_selecionado] # Checar apenas o escolhido

            barbeiros_disponiveis_validos = []
            for b in barbeiros_a_checar:
                # 5.1 Validação Almoço
                if is_horario_almoco(dia_da_semana_agendamento, hora_agendamento_int, b):
                    if barbeiro_selecionado != "Sem preferência": st.warning(f"{b} está em almoço neste horário."); continue # Só avisa se foi escolha específica
                    continue # Pula para o próximo barbeiro se for sem preferência

                # 5.2 Validação Visagismo
                if is_visagismo_selecionado and b != "Lucas Borges":
                    if barbeiro_selecionado != "Sem preferência": st.error("Apenas Lucas Borges realiza atendimentos de visagismo."); st.stop()
                    continue

                # 5.3 Validação Estado Atual (Firestore)
                estado_atual_b, _ = obter_estado_horario(data_agendamento_str, horario_agendamento, b)

                # Estados que PERMITEM tentar agendar (função de salvar valida Pezim vs Bloqueado)
                estados_permissiveis = [ESTADO_DISPONIVEL, ESTADO_PEZIM_AGENDADO]

                if estado_atual_b not in estados_permissiveis:
                    if barbeiro_selecionado != "Sem preferência": st.error(f"O horário {horario_agendamento} está {estado_atual_b.lower()} para {b}."); st.stop()
                    continue # Tenta o próximo barbeiro

                # 5.4 Validação Combo (Próximo Horário)
                if is_combo_corte_barba:
                    if not verificar_disponibilidade_horario_seguinte(data_agendamento_str, horario_agendamento, b):
                         horario_seguinte_str = (horario_obj_agendamento + timedelta(minutes=30)).strftime('%H:%M')
                         if barbeiro_selecionado != "Sem preferência": st.error(f"{b} não pode fazer Corte+Barba, pois o horário seguinte ({horario_seguinte_str}) não está disponível."); st.stop()
                         continue

                # Se passou por todas as validações para este barbeiro 'b'
                barbeiros_disponiveis_validos.append(b)

            # 5.5 Selecionar ou Falhar
            if not barbeiros_disponiveis_validos:
                st.error("Nenhum barbeiro disponível que atenda a todas as condições (horário, tipo de serviço, almoço, disponibilidade para combo).")
                st.stop()
            elif len(barbeiros_disponiveis_validos) == 1:
                barbeiro_final = barbeiros_disponiveis_validos[0]
                if barbeiro_selecionado == "Sem preferência": st.info(f"Barbeiro '{barbeiro_final}' selecionado automaticamente.")
            else: # Mais de um disponível (caso "Sem preferência")
                barbeiro_final = random.choice(barbeiros_disponiveis_validos)
                st.info(f"Barbeiro '{barbeiro_final}' selecionado aleatoriamente entre os disponíveis.")

            # --- 6. Tentar Salvar Agendamento ---
            if barbeiro_final:
                dados_agendamento_salvo = salvar_ou_atualizar_agendamento(
                    data_str=data_agendamento_str,
                    horario=horario_agendamento,
                    nome=nome,
                    telefone=telefone,
                    servicos_novos=servicos_selecionados,
                    barbeiro=barbeiro_final
                )

                # 7. Pós-Salvamento (Bloqueio de Combo, Email, Feedback)
                if dados_agendamento_salvo:
                    # Bloquear próximo horário se for combo
                    bloqueio_necessario = is_combo_corte_barba
                    erro_bloqueio = False
                    if bloqueio_necessario:
                        if not bloquear_horario_seguinte(data_agendamento_str, horario_agendamento, barbeiro_final):
                            st.warning("Agendamento principal salvo, mas houve um erro ao bloquear o horário seguinte automaticamente. Verifique a agenda ou contate o suporte.")
                            erro_bloqueio = True

                    # Montar resumo com base nos dados retornados (do agendamento específico)
                    resumo = f"""
Resumo do Agendamento Confirmado:
Nome: {dados_agendamento_salvo.get('cliente_nome', 'N/A')}
Telefone: {dados_agendamento_salvo.get('cliente_telefone', 'N/A')}
Data: {data_agendamento_str}
Horário: {horario_agendamento}
Barbeiro: {barbeiro_final}
Serviços: {', '.join(dados_agendamento_salvo.get('servicos', []))}
ID (interno): {dados_agendamento_salvo.get('id_agendamento', 'N/A')}
"""
                    enviar_email("Novo Agendamento Confirmado", resumo)
                    st.success("Agendamento confirmado com sucesso!")
                    st.markdown("```\n" + resumo + "\n```")

                    if bloqueio_necessario and not erro_bloqueio:
                        horario_seguinte_str = (horario_obj_agendamento + timedelta(minutes=30)).strftime('%H:%M')
                        st.info(f"O horário das {horario_seguinte_str} foi bloqueado para {barbeiro_final} devido ao combo.")

                    time.sleep(6) # Pausa para ler
                    st.rerun() # Recarrega para atualizar a tabela

                else:
                    # Erro já foi mostrado por salvar_ou_atualizar_agendamento
                    # st.error("Não foi possível concluir o agendamento.") # Mensagem redundante
                    pass # A função interna já deve ter mostrado o erro
            else:
                # Caso não tenha conseguido definir um barbeiro_final (não deveria acontecer com a lógica acima)
                 st.error("Erro inesperado: Não foi possível determinar um barbeiro.")


# --- Formulário de Cancelamento ---
with st.form("cancelar_form"):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Telefone usado no agendamento")

    # Usar um date_input para selecionar a data do cancelamento
    data_cancelar_obj = st.date_input(
        "Data do Agendamento a Cancelar",
        min_value=datetime.today().date(), # Permite cancelar agendamentos futuros ou de hoje
        key="data_cancelar_widget"
    )
    data_cancelar_str = data_cancelar_obj.strftime('%d/%m/%Y') # Formata para string

    horarios_base_cancelamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
    horario_cancelar = st.selectbox("Horário do Agendamento", horarios_base_cancelamento)
    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento", barbeiros)

    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")

    if submitted_cancelar:
        if not telefone_cancelar:
            st.warning("Por favor, informe o telefone utilizado no agendamento.")
        else:
            with st.spinner("Processando cancelamento..."):
                # Chama a nova função que executa a transação
                agendamentos_cancelados, erro_msg = executar_cancelamento(
                    data_cancelar_str, horario_cancelar, telefone_cancelar, barbeiro_cancelar
                )

                if agendamentos_cancelados: # Verifica se a lista não é None e não está vazia
                    st.success("Agendamento(s) cancelado(s) com sucesso!")

                    # Montar resumo (pode mostrar todos ou só o primeiro)
                    resumo_completo = "Detalhes do(s) Agendamento(s) Cancelado(s):\n"
                    primeiro_cancelado = None
                    for idx, ag_canc in enumerate(agendamentos_cancelados):
                        if idx == 0: primeiro_cancelado = ag_canc # Guarda o primeiro para checar combo
                        resumo_completo += f"""
--- Agendamento {idx+1} ---
Nome: {ag_canc.get('cliente_nome', 'N/A')}
Telefone: {ag_canc.get('cliente_telefone', 'N/A')}
Data: {ag_canc.get('data_formatada', data_cancelar_str)}
Horário: {ag_canc.get('horario', horario_cancelar)}
Barbeiro: {ag_canc.get('barbeiro', barbeiro_cancelar)}
Serviços: {', '.join(ag_canc.get('servicos', []))}
ID (interno): {ag_canc.get('id_agendamento', 'N/A')}
"""
                    enviar_email("Agendamento Cancelado", resumo_completo)
                    st.markdown("```\n" + resumo_completo + "\n```")

                    # --- Lógica para Desbloquear Horário Seguinte ---
                    # Usar os dados do *primeiro* agendamento cancelado para verificar o combo
                    # (Assumindo que um combo é sempre um único registro na lista)
                    if primeiro_cancelado:
                        servicos_cancelados = primeiro_cancelado.get('servicos', [])
                        if "Barba" in servicos_cancelados and any(c in servicos_cancelados for c in SERVICOS_QUE_BLOQUEIAM_HORARIO_SEGUINTE):
                            try:
                                horario_original_str = primeiro_cancelado['horario']
                                data_original_str = primeiro_cancelado['data_formatada'] # Usar data formatada
                                barbeiro_original = primeiro_cancelado['barbeiro']

                                horario_original_dt = datetime.strptime(horario_original_str, '%H:%M')
                                horario_seguinte_dt = horario_original_dt + timedelta(minutes=30)
                                horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')

                                desbloquear_horario(data_original_str, horario_seguinte_str, barbeiro_original)
                                st.info(f"O horário seguinte ({horario_seguinte_str}) foi desbloqueado, pois um combo foi cancelado.")

                            except KeyError as e:
                                st.warning(f"Não foi possível determinar os dados para desbloquear o horário seguinte (campo faltando: {e}).")
                            except ValueError as e:
                                st.warning(f"Erro ao calcular horário seguinte para desbloqueio: {e}")
                            except Exception as e:
                                st.warning(f"Erro inesperado ao tentar desbloquear horário seguinte: {e}")

                    time.sleep(7) # Pausa maior para ler tudo
                    st.rerun()

                else:
                    # Mostrar mensagem de erro retornada pela função de cancelamento
                    st.error(f"Não foi possível cancelar: {erro_msg}")
                    st.info("Dica: Verifique se todos os dados (telefone, data, horário, barbeiro) estão corretos e correspondem exatamente aos do agendamento original.")

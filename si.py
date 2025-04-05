# -*- coding: utf-8 -*- # Adicionado para garantir codificação correta
import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
import google.api_core.retry as retry
import random
# import pandas as pd # Não parece ser usado, pode remover se não precisar
import time

# --- Estilos CSS ---
st.markdown(
    """
    <style>
        table {
            display: block !important;
            width: fit-content !important; /* Ou 100% se preferir */
            /* table-layout: fixed; /* Descomente se as larguras não forem respeitadas */
        }
        div[data-testid="stForm"] {
            display: block !important;
        }
        /* Status da Tabela */
        .status-disponivel { background-color: forestgreen; color: white; }
        .status-ocupado { background-color: firebrick; color: white; }
        .status-indisponivel { background-color: orange; color: white; }
        .status-extra { background-color: #1E90FF; color: white; } /* Azul Pezim */

        /* Largura Coluna Barbeiro */
        th.barber-col {
            width: 40%; /* Ajuste % */
            text-align: center;
        }
        /* Ajuste opcional Coluna Horário */
        /* th:first-child { width: 20%; } */

    </style>
    """,
    unsafe_allow_html=True,
)

# --- Carregamento de Credenciais (Firebase e Email) ---
FIREBASE_CREDENTIALS = None
EMAIL = None
SENHA = None
try:
    firebase_credentials_json = st.secrets["firebase"]["FIREBASE_CREDENTIALS"]
    FIREBASE_CREDENTIALS = json.loads(firebase_credentials_json)
    EMAIL = st.secrets["email"]["EMAIL_CREDENCIADO"]
    SENHA = st.secrets["email"]["EMAIL_SENHA"]
except KeyError as e:
    st.error(f"Chave ausente no secrets.toml: {e}")
    st.stop() # Para a execução se credenciais faltarem
except json.JSONDecodeError as e:
    st.error(f"Erro JSON nas credenciais Firebase: {e}")
    st.stop()
except Exception as e:
    st.error(f"Erro inesperado no carregamento: {e}")
    st.stop()

# --- Inicialização Firebase ---
if FIREBASE_CREDENTIALS:
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"Erro ao inicializar Firebase: {e}")
            st.stop() # Para se Firebase não inicializar

# --- Cliente Firestore ---
db = firestore.client() if firebase_admin._apps else None
if not db:
     st.error("Falha ao conectar com Firestore.")
     st.stop() # Para se não conectar ao DB

# --- Dados Básicos ---
servicos = {
    "Tradicional": 15, "Social": 18, "Degradê": 23, "Pezim": 7,
    "Navalhado": 25, "Barba": 15, "Abordagem de visagismo": 45,
    "Consultoria de visagismo": 65,
}
lista_servicos = list(servicos.keys())
barbeiros = ["Lucas Borges", "Aluizio"]

# --- Funções Auxiliares ---

def enviar_email(assunto, mensagem):
    """Envia email de notificação."""
    # Verifica se as credenciais de email foram carregadas
    if not EMAIL or not SENHA:
         st.warning("Credenciais de e-mail não configuradas. E-mail não enviado.")
         print("WARN: Email credentials not set.")
         return
    try:
        msg = MIMEText(mensagem, 'plain', 'utf-8') # Especifica encoding
        msg['Subject'] = assunto
        msg['From'] = EMAIL
        msg['To'] = EMAIL # Envia para o próprio email
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL, SENHA)
            server.sendmail(EMAIL, EMAIL, msg.as_string())
            print(f"INFO: Email '{assunto}' enviado.") # Log de sucesso
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")
        print(f"ERROR: Email sending failed: {e}") # Log de erro

# Função salvar_agendamento CORRIGIDA (com return True/False)
def salvar_agendamento(data, horario, nome, telefone, servicos_lista, barbeiro):
    """Salva um novo agendamento usando transação. Retorna True ou False."""
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        data_obj = datetime.strptime(data, '%d/%m/%Y')
    except ValueError:
        st.error(f"Formato de data inválido ao salvar: {data}")
        return False # Retorna False

    # Define a função transacional interna
    @firestore.transactional
    def _transacao_salvar(transaction, ref, dados):
        doc = ref.get(transaction=transaction)
        if doc.exists:
            raise ValueError("Horário já ocupado (concorrência).")
        transaction.set(ref, dados)

    dados_agendamento = {
        'nome': nome, 'telefone': telefone, 'servicos': servicos_lista,
        'barbeiro': barbeiro, 'data': data_obj, 'horario': horario,
        'timestamp_criacao': firestore.SERVER_TIMESTAMP
    }

    transaction = db.transaction()
    try:
        _transacao_salvar(transaction, agendamento_ref, dados_agendamento)
        print(f"INFO: Agendamento salvo com sucesso para {chave_agendamento}")
        return True # Retorna True
    except ValueError as e:
        # Erro específico (horário ocupado detectado pela transação)
        # A lógica principal que chamou deve mostrar msg "ocupado"
        print(f"WARN: Transação falhou (provável concorrência) para {chave_agendamento}: {e}")
        st.error(f"Conflito de agendamento detectado para {horario}. Tente novamente.") # Informa o usuário
        return False # Retorna False
    except Exception as e:
        st.error(f"Erro inesperado na transação ao salvar: {e}")
        st.exception(e)
        return False # Retorna False

# Função cancelar_agendamento CORRIGIDA (com servicos_cancelados e data_str)
def cancelar_agendamento(data, horario, telefone, barbeiro):
    """Cancela agendamento. Retorna dados do agendamento ou None."""
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict().get('telefone') == telefone:
            agendamento_data = doc.to_dict()
            servicos_cancelados = agendamento_data.get('servicos', []) # Guarda serviços
            data_firestore = agendamento_data.get('data')

            # Formata data para string 'dd/mm/yyyy' em 'data_str'
            if isinstance(data_firestore, datetime):
                 agendamento_data['data_str'] = data_firestore.strftime('%d/%m/%Y')
            elif isinstance(data_firestore, str):
                 try: # Tenta reformatar se for string
                      agendamento_data['data_str'] = datetime.strptime(data_firestore, '%d/%m/%Y').strftime('%d/%m/%Y')
                 except ValueError:
                      agendamento_data['data_str'] = data_firestore # Mantém se não for dd/mm/yyyy
            else:
                 agendamento_data['data_str'] = "Data inválida"

            agendamento_ref.delete() # Deleta
            agendamento_data['servicos_cancelados'] = servicos_cancelados # Adiciona serviços ao retorno
            print(f"INFO: Agendamento cancelado: {chave_agendamento}")
            return agendamento_data
        else:
            print(f"WARN: Cancelamento falhou (não encontrado ou telefone incorreto): {chave_agendamento}")
            return None
    except Exception as e:
        st.error(f"Erro ao acessar Firestore para cancelar: {e}")
        print(f"ERROR: Firestore access failed during cancellation: {e}")
        return None

# Função desbloquear_horario (Mantida)
def desbloquear_horario(data, horario, barbeiro):
    """Remove um documento de bloqueio."""
    # A data aqui deve ser string 'dd/mm/yyyy'
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    agendamento_ref = db.collection('agendamentos').document(chave_bloqueio)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict().get('nome') == "BLOQUEADO":
            agendamento_ref.delete()
            print(f"INFO: Horário {horario} {data} de {barbeiro} desbloqueado.")
    except Exception as e:
        st.error(f"Erro ao desbloquear horário: {e}")
        print(f"ERROR: Failed to unblock slot {chave_bloqueio}: {e}")

# Função verificar_disponibilidade MODIFICADA
@st.cache_data(ttl=60) # Cache de 60s
def verificar_disponibilidade(data, horario, barbeiro=None):
    """Verifica status: None (livre), "BLOQUEADO", dict (ocupado), ou erro."""
    if not db: return "ERRO_DB"
    if not barbeiro: return "ERRO_BARBEIRO"

    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)

    try:
        doc_bloqueio = bloqueio_ref.get()
        if doc_bloqueio.exists and doc_bloqueio.to_dict().get('nome') == "BLOQUEADO":
            return "BLOQUEADO"
        doc_agendamento = agendamento_ref.get()
        if doc_agendamento.exists:
            return doc_agendamento.to_dict()
        return None # Livre
    except google.api_core.exceptions.RetryError as e:
        print(f"WARN: Firestore connection error: {e}")
        # st.warning(f"Problema de conexão ao verificar {horario}. Tentando novamente...") # Mensagem opcional
        return "ERRO_CONEXAO" # Indica erro tratável (retry pode funcionar)
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        print(f"ERROR: Unexpected availability check error: {e}")
        return "ERRO_INESPERADO"

# Função verificar_disponibilidade_horario_seguinte MODIFICADA
@retry.Retry() # Usa retry para erros de conexão
def verificar_disponibilidade_horario_seguinte(data, horario_atual, barbeiro):
    """Verifica se próximo slot está livre (status None)."""
    if not db: return False
    try:
        horario_seguinte_dt = datetime.strptime(horario_atual, '%H:%M') + timedelta(minutes=30)
        if horario_seguinte_dt.hour >= 20: return False # Fim do expediente
        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
        status_seguinte = verificar_disponibilidade(data, horario_seguinte_str, barbeiro)
        # Considera erro de conexão como indisponível para segurança
        if status_seguinte == "ERRO_CONEXAO": return False
        # Qualquer outro erro inesperado, melhor bloquear também
        if isinstance(status_seguinte, str) and "ERRO" in status_seguinte: return False
        return status_seguinte is None # Livre se for None
    except ValueError: return False # Erro no strptime
    except Exception as e:
        print(f"ERROR: Checking next slot availability failed: {e}")
        return False

# Função bloquear_horario CORRIGIDA (salva data objeto)
def bloquear_horario(data, horario, barbeiro):
    """Cria um documento de bloqueio."""
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    data_str = data if isinstance(data, str) else data.strftime('%d/%m/%Y')
    try:
        data_obj_para_salvar = datetime.strptime(data_str, '%d/%m/%Y')
    except ValueError:
        st.error(f"Erro ao converter data '{data_str}' para bloqueio.")
        return # Aborta se data for inválida

    try:
        db.collection('agendamentos').document(chave_bloqueio).set({
            'nome': "BLOQUEADO", 'telefone': "BLOQUEADO", 'servicos': ["BLOQUEADO"],
            'barbeiro': barbeiro, 'data': data_obj_para_salvar, 'horario': horario,
            'timestamp_criacao': firestore.SERVER_TIMESTAMP
        })
        print(f"INFO: Horário {horario} {data_str} de {barbeiro} bloqueado.")
    except Exception as e:
         st.error(f"Erro ao salvar bloqueio: {e}")
         print(f"ERROR: Saving block failed {chave_bloqueio}: {e}")


# --- Interface Streamlit ---
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

# Estado da Data
if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().date()

def handle_date_change():
    st.session_state.data_agendamento = st.session_state.get("data_input_widget", datetime.today().date())
    verificar_disponibilidade.clear() # Limpa cache da disponibilidade

# Seletor de Data
data_agendamento_obj = st.date_input(
    "Data para visualizar disponibilidade",
    value=st.session_state.data_agendamento, # Usa valor do estado
    min_value=datetime.today().date(),
    key="data_input_widget", # Chave para acessar o valor
    on_change=handle_date_change
)
# Garante que o objeto de data esteja atualizado e formata
data_agendamento_obj = st.session_state.get("data_input_widget", datetime.today().date())
data_para_tabela = data_agendamento_obj.strftime('%d/%m/%Y')

# --- Tabela de Disponibilidade ---
st.subheader("Disponibilidade dos Barbeiros")

# Cabeçalho da Tabela
html_table = '<table style="font-size: 14px; border-collapse: collapse; width: 100%; border: 1px solid #ddd;"><tr><th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>'
for barbeiro in barbeiros:
    html_table += f'<th class="barber-col" style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">{barbeiro}</th>'
html_table += '</tr>'

# Geração Linhas da Tabela
dia_da_semana_tabela = data_agendamento_obj.weekday()
horarios_tabela = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

for horario in horarios_tabela:
    html_table += f'<tr><td style="padding: 8px; border: 1px solid #ddd;">{horario}</td>'
    for barbeiro in barbeiros:
        status_texto = "Erro"
        status_classe_css = "status-indisponivel" # Padrão
        hora_int = int(horario.split(':')[0])

        # 1. Almoço (Lógica Corrigida com Parênteses)
        em_almoco = False
        if dia_da_semana_tabela < 5: # Seg-Sex
            if barbeiro == "Lucas Borges" and (hora_int == 12 or hora_int == 13): em_almoco = True
            elif barbeiro == "Aluizio" and (hora_int == 11 or hora_int == 12): em_almoco = True

        if em_almoco:
            status_texto = "Indisponível"; status_classe_css = "status-indisponivel"
        else:
            # 2. Verifica Disponibilidade
            status = verificar_disponibilidade(data_para_tabela, horario, barbeiro)
            if status is None: status_texto = "Disponível"; status_classe_css = "status-disponivel"
            elif status == "BLOQUEADO": status_texto = "Indisponível"; status_classe_css = "status-indisponivel"
            elif isinstance(status, dict): # Ocupado
                 servicos_no_horario = status.get('servicos', [])
                 if servicos_no_horario == ["Pezim"]: status_texto = "Serviço extra (rápido)"; status_classe_css = "status-extra"
                 else: status_texto = "Ocupado"; status_classe_css = "status-ocupado" # Inclui 2 Pezins ou Pezim+Outro
            else: status_texto = f"Erro ({status})"; status_classe_css = "status-indisponivel" # Erro de verificação

        html_table += f'<td class="{status_classe_css}" style="padding: 8px; border: 1px solid #ddd; text-align: center; height: 30px;">{status_texto}</td>'
    html_table += '</tr>'
html_table += '</table>'
st.markdown(html_table, unsafe_allow_html=True)

# --- Formulário de Agendamento ---
with st.form("agendar_form"):
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome")
    telefone = st.text_input("Telefone")
    # Data vem do seletor acima (data_para_tabela)
    horarios_base_agendamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
    barbeiro_selecionado = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])
    horario_agendamento = st.selectbox("Horário", horarios_base_agendamento)
    servicos_selecionados = st.multiselect("Serviços", lista_servicos)

    # Preços
    st.write("Preços dos serviços:")
    servicos_com_preco = {s: f"R$ {p}" for s, p in servicos.items()}
    for srv, preco in servicos_com_preco.items(): st.write(f"{srv}: {preco}")

    submitted = st.form_submit_button("Confirmar Agendamento")

# --- Processamento Agendamento ---
if submitted:
    verificar_disponibilidade.clear(); time.sleep(0.5) # Limpa cache

    with st.spinner("Processando agendamento..."):
        erro = False; mensagem_erro = ""; barbeiro_final = None
        acao_necessaria = "NENHUMA"; chave_para_atualizar = None
        status_horario_escolhido = None; precisa_bloquear_proximo = False
        data_agendamento_str = data_para_tabela # Usa data do seletor

        # 1. Validações Iniciais
        if not nome or not telefone or not servicos_selecionados or not horario_agendamento:
            erro = True; mensagem_erro = "Preencha Nome, Telefone, Horário e selecione Serviço(s)."
        if not erro:
            tem_visagismo = any(s in servicos_selecionados for s in ["Abordagem de visagismo", "Consultoria de visagismo"])
            tem_pezim = "Pezim" in servicos_selecionados
            if tem_visagismo and barbeiro_selecionado == "Aluizio": erro = True; mensagem_erro = "Visagismo apenas com Lucas Borges."
            elif tem_visagismo and tem_pezim: erro = True; mensagem_erro = "Visagismo e Pezim não podem ser agendados juntos."

        # 2. Verificação Principal Disponibilidade/Almoço/Pezim
        if not erro:
            try:
                barbeiros_a_verificar = barbeiros if barbeiro_selecionado == "Sem preferência" else [barbeiro_selecionado]
                if barbeiro_selecionado not in barbeiros and barbeiro_selecionado != "Sem preferência":
                     erro = True; mensagem_erro = f"Barbeiro '{barbeiro_selecionado}' inválido."

                if not erro:
                     for b in barbeiros_a_verificar:
                          # 2.1 Almoço (Lógica Corrigida com Parênteses)
                          data_obj_ag = datetime.strptime(data_agendamento_str, '%d/%m/%Y')
                          dia_sem = data_obj_ag.weekday()
                          hora_ag = int(horario_agendamento.split(':')[0])
                          em_almoco = False
                          if dia_sem < 5:
                               if b == "Lucas Borges" and (hora_ag == 12 or hora_ag == 13): em_almoco = True
                               elif b == "Aluizio" and (hora_ag == 11 or hora_ag == 12): em_almoco = True
                          if em_almoco: status_horario_escolhido = "ALMOÇO"; continue

                          # 2.2 Status Horário
                          status_horario = verificar_disponibilidade(data_agendamento_str, horario_agendamento, b)

                          # Determina Ação
                          if status_horario is None: # Livre
                               barbeiro_final = b; acao_necessaria = "CRIAR"; status_horario_escolhido = status_horario; break
                          elif isinstance(status_horario, dict): # Ocupado
                               serv_exist = status_horario.get('servicos', [])
                               if serv_exist == ["Pezim"]: # Um Pezim
                                    if servicos_selecionados == ["Pezim"]: # Add 2º Pezim
                                         barbeiro_final = b; acao_necessaria = "ATUALIZAR_PARA_DUPLO_PEZIM"; chave_para_atualizar = f"{data_agendamento_str}_{horario_agendamento}_{b}"; status_horario_escolhido = status_horario; break
                                    else: # Add Outro Serviço?
                                         permitidos = {"Barba", "Tradicional", "Social"}
                                         if set(servicos_selecionados).issubset(permitidos): # Permitido?
                                              barbeiro_final = b; acao_necessaria = "ATUALIZAR_COM_OUTRO"; chave_para_atualizar = f"{data_agendamento_str}_{horario_agendamento}_{b}"; status_horario_escolhido = status_horario; break
                                         else: # Não Permitido
                                              nao_permitidos = set(servicos_selecionados) - permitidos
                                              erro = True; mensagem_erro = f"Serviço(s) '{', '.join(nao_permitidos)}' não permitido(s) com Pezim."; status_horario_escolhido = status_horario; break
                               else: status_horario_escolhido = status_horario; continue # Já Ocupado (2 Pezins, etc)
                          elif status_horario == "BLOQUEADO": status_horario_escolhido = status_horario; continue
                          elif isinstance(status_horario, str) and "ERRO" in status_horario: erro = True; mensagem_erro = f"Erro verificação ({status_horario})."; break
                          if erro: break # Sai loop interno

            except ValueError: erro = True; mensagem_erro = "Formato data/hora inválido."
            except Exception as e: erro = True; mensagem_erro = f"Erro inesperado: {e}"; st.exception(e)

            # 3. Pós-Loop Check
            if not erro and acao_necessaria == "NENHUMA":
                 if status_horario_escolhido == "ALMOÇO": mensagem_erro = f"Barbeiro(s) em almoço ({horario_agendamento})."
                 elif status_horario_escolhido == "BLOQUEADO": mensagem_erro = f"Horário {horario_agendamento} indisponível (bloqueado)."
                 elif isinstance(status_horario_escolhido, dict):
                      if status_horario_escolhido.get('servicos', []) == ["Pezim", "Pezim"]: mensagem_erro = f"Horário {horario_agendamento} ocupado (2 Pezins)."
                      else: mensagem_erro = f"Horário {horario_agendamento} já ocupado."
                 else: mensagem_erro = f"Horário {horario_agendamento} indisponível p/ {barbeiro_selecionado}."
                 erro = True

            # 4. Corte+Barba Check
            if not erro:
                 tem_barba = "Barba" in servicos_selecionados
                 tem_corte = any(c in servicos_selecionados for c in ["Tradicional", "Social", "Degradê", "Navalhado"])
                 if tem_barba and tem_corte and acao_necessaria != "ATUALIZAR_PARA_DUPLO_PEZIM":
                     if not verificar_disponibilidade_horario_seguinte(data_agendamento_str, horario_agendamento, barbeiro_final):
                         horario_seg = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                         erro = True; mensagem_erro = f"Agendar Corte+Barba falhou: Horário seguinte ({horario_seg}) indisponível."
                     else: precisa_bloquear_proximo = True

            # 5. Executar Ação DB
            if not erro:
                sucesso = False
                if acao_necessaria == "CRIAR":
                    sucesso = salvar_agendamento(data_agendamento_str, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_final)
                    # Confia no erro interno de salvar_agendamento
                elif acao_necessaria == "ATUALIZAR_PARA_DUPLO_PEZIM":
                    try: db.collection('agendamentos').document(chave_para_atualizar).update({'servicos': ["Pezim", "Pezim"]}); sucesso = True
                    except Exception as e: st.error(f"Erro BD (duplo P): {e}")
                elif acao_necessaria == "ATUALIZAR_COM_OUTRO":
                    try: db.collection('agendamentos').document(chave_para_atualizar).update({'servicos': ["Pezim"] + servicos_selecionados}); sucesso = True
                    except Exception as e: st.error(f"Erro BD (P+Outro): {e}")

                # 6. Pós Sucesso DB
                if sucesso:
                    if precisa_bloquear_proximo:
                         horario_seg = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                         bloquear_horario(data_agendamento_str, horario_seg, barbeiro_final); st.info(f"Horário das {horario_seg} bloqueado.")
                    resumo = f"Nome: {nome}; Tel: {telefone}; Data: {data_agendamento_str} {horario_agendamento}; Barb: {barbeiro_final}; Serv: {', '.join(servicos_selecionados)}"
                    if "ATUALIZAR" in acao_necessaria: st.success("Agendamento atualizado!")
                    else: st.success("Agendamento confirmado!")
                    st.info(f"Resumo: {resumo}")
                    enviar_email("Agendamento Confirmado/Atualizado", resumo)
                    verificar_disponibilidade.clear(); time.sleep(10); st.rerun()

        # 7. Exibir Erro Final (se 'erro' foi True ANTES da ação no DB)
        if erro and mensagem_erro:
            st.error(mensagem_erro)
        # Não precisamos do 'elif erro:' genérico se as msgs específicas estão sendo setadas

# --- Formulário de Cancelamento ---
with st.form("cancelar_form"):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Telefone para Cancelamento")
    data_cancelar_obj = st.date_input("Data do Agendamento", min_value=datetime.today().date()) # Usa min_value correto
    horarios_base_cancelamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
    horario_cancelar = st.selectbox("Horário do Agendamento", horarios_base_cancelamento)
    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento", barbeiros)
    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")

    # Processamento Cancelamento
    if submitted_cancelar:
         verificar_disponibilidade.clear(); time.sleep(0.5)
         with st.spinner("Processando cancelamento..."):
            try:
                data_cancelar_str = data_cancelar_obj.strftime('%d/%m/%Y')
                cancelado_data = cancelar_agendamento(data_cancelar_str, horario_cancelar, telefone_cancelar, barbeiro_cancelar)

                if cancelado_data is not None: # Sucesso
                    servicos_cancelados = cancelado_data.get('servicos_cancelados', [])
                    resumo = f"Cancelado: Nome: {cancelado_data.get('nome', 'N/A')}; Tel: {cancelado_data.get('telefone', 'N/A')}; Data: {cancelado_data.get('data_str', 'N/A')} {cancelado_data.get('horario', 'N/A')}; Barb: {cancelado_data.get('barbeiro', 'N/A')}; Serv: {', '.join(servicos_cancelados)}"
                    enviar_email("Agendamento Cancelado", resumo)
                    st.success("Agendamento cancelado!"); st.info(resumo)

                    # Desbloqueio
                    if "Barba" in servicos_cancelados and any(c in servicos_cancelados for c in ["Tradicional", "Social", "Degradê", "Navalhado"]):
                         try:
                             h_canc = cancelado_data.get('horario'); d_canc_str = cancelado_data.get('data_str'); b_canc = cancelado_data.get('barbeiro')
                             if h_canc and d_canc_str and b_canc and d_canc_str != "Data inválida":
                                 h_seg_dt = datetime.strptime(h_canc, '%H:%M') + timedelta(minutes=30)
                                 if h_seg_dt.hour < 20:
                                     h_seg_str = h_seg_dt.strftime('%H:%M')
                                     desbloquear_horario(d_canc_str, h_seg_str, b_canc)
                                     st.info(f"Horário seguinte ({h_seg_str}) desbloqueado.")
                             else: st.warning("Dados insuficientes para desbloquear.")
                         except Exception as e_desbl: st.error(f"Erro desbloqueio: {e_desbl}")

                    verificar_disponibilidade.clear(); time.sleep(5); st.rerun()
                else: # Falha
                    st.error("Agendamento não encontrado com os dados informados.")
            except AttributeError: st.error("Erro: Não foi possível obter data selecionada.")
            except Exception as e: st.error(f"Erro inesperado cancelamento: {e}"); st.exception(e)

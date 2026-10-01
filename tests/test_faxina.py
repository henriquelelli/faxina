import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from faxina import config, relatorio
from faxina.cli import faxinar
from faxina.config import Comando, ConfigInvalida, Pasta, Projetos, Regeneravel, RepoGit
from faxina.disco import RemocaoRecusada, formatar_bytes, tamanho_em_disco, verificar_remocao
from faxina.tarefas import (
    ERRO, LIMPO, MANTIDO, SIMULADO, Processo, compactar_git, executar_comandos,
    limpar_pastas, limpar_projetos, objetos_soltos,
)

DIA = 86400
AGORA = 1_900_000_000.0
LOCKS = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")
NODE = Regeneravel("Dependências Node", "node_modules", LOCKS)


def escrever(caminho: Path, conteudo: str = "x", idade_dias: float = 0) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(conteudo)
    momento = AGORA - idade_dias * DIA
    os.utime(caminho, (momento, momento))
    return caminho


def projeto(raiz: Path, nome: str, idade: float, lock: bool = True, mb: int = 1) -> Path:
    p = raiz / nome
    escrever(p / "package.json", "{}", idade)
    escrever(p / "src" / "index.js", "1", idade)
    if lock:
        escrever(p / "package-lock.json", "{}", idade)
    escrever(p / "node_modules" / "pacote" / "grande.bin", "0" * (mb * 1_000_000), idade)
    return p


class TestProjetos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(os.path.realpath(self.tmp.name))
        self.cfg = Projetos(ativo=True, raizes=(self.raiz,), dias_sem_uso=14, regeneraveis=(NODE,))

    def tearDown(self):
        self.tmp.cleanup()

    def rodar(self, simular=False, processos=None, cfg=None):
        return {Path(r.alvo).parent.name: r for r in
                limpar_projetos(cfg or self.cfg, simular, AGORA, processos if processos is not None else [])}

    def test_apaga_parado_e_mantem_ativo(self):
        projeto(self.raiz, "parado", idade=30)
        projeto(self.raiz, "ativo", idade=2)
        r = self.rodar()
        self.assertEqual(r["parado"].situacao, LIMPO)
        self.assertGreater(r["parado"].bytes, 900_000)
        self.assertFalse((self.raiz / "parado" / "node_modules").exists())
        self.assertTrue((self.raiz / "parado" / "src" / "index.js").exists())
        self.assertEqual(r["ativo"].situacao, MANTIDO)
        self.assertIn("há 2 dias", r["ativo"].detalhe)
        self.assertTrue((self.raiz / "ativo" / "node_modules").exists())

    def test_um_arquivo_recente_basta_para_manter(self):
        p = projeto(self.raiz, "misto", idade=60)
        escrever(p / "docs" / "notas.md", "novo", idade_dias=1)
        self.assertEqual(self.rodar()["misto"].situacao, MANTIDO)

    def test_arquivos_dentro_de_node_modules_nao_contam_como_uso(self):
        p = projeto(self.raiz, "instalado", idade=60)
        escrever(p / "node_modules" / "recente.js", "1", idade_dias=0)
        self.assertEqual(self.rodar()["instalado"].situacao, LIMPO)

    def test_sem_lockfile_nao_apaga(self):
        projeto(self.raiz, "sem-lock", idade=60, lock=False)
        r = self.rodar()["sem-lock"]
        self.assertEqual(r.situacao, MANTIDO)
        self.assertIn("não daria para reconstruir", r.detalhe)

    def test_lockfile_na_pasta_de_cima_vale_para_monorepo(self):
        escrever(self.raiz / "mono" / "pnpm-lock.yaml", "x", 60)
        escrever(self.raiz / "mono" / "apps" / "web" / "package.json", "{}", 60)
        escrever(self.raiz / "mono" / "apps" / "web" / "node_modules" / "a.js", "1", 60)
        self.assertEqual(self.rodar()["web"].situacao, LIMPO)

    def test_processo_aberto_no_projeto_impede(self):
        p = projeto(self.raiz, "em-uso", idade=60)
        processos = [Processo(4242, "node", p / "src")]
        r = self.rodar(processos=processos)["em-uso"]
        self.assertEqual(r.situacao, MANTIDO)
        self.assertIn("pid 4242", r.detalhe)

    def test_sem_lista_de_processos_nada_e_apagado(self):
        projeto(self.raiz, "parado", idade=60)
        r = limpar_projetos(self.cfg, False, AGORA, None)[0]
        self.assertEqual(r.situacao, MANTIDO)
        self.assertTrue((self.raiz / "parado" / "node_modules").exists())

    def test_padrao_ignorar(self):
        projeto(self.raiz, "protegido", idade=60)
        cfg = Projetos(ativo=True, raizes=(self.raiz,), regeneraveis=(NODE,), ignorar=("*/protegido",))
        self.assertIn("ignorado", self.rodar(cfg=cfg)["protegido"].detalhe)

    def test_link_simbolico_nao_e_seguido(self):
        real = projeto(self.raiz, "real", idade=60)
        fora = self.raiz / "fora"
        fora.mkdir()
        (fora / "node_modules").symlink_to(real / "node_modules")
        escrever(fora / "package-lock.json", "{}", 60)
        resultados = limpar_projetos(self.cfg, True, AGORA, [])
        self.assertEqual([Path(r.alvo).parent.name for r in resultados], ["real"])

    def test_simulacao_nao_apaga_e_e_deterministica(self):
        for nome, idade in (("b", 40), ("a", 50), ("c", 1)):
            projeto(self.raiz, nome, idade)
        primeira = limpar_projetos(self.cfg, True, AGORA, [])
        segunda = limpar_projetos(self.cfg, True, AGORA, [])
        chave = lambda rs: [(r.alvo, r.situacao, r.bytes, r.detalhe) for r in rs]
        self.assertEqual(chave(primeira), chave(segunda))
        self.assertEqual([Path(r.alvo).parent.name for r in primeira], ["a", "b", "c"])
        self.assertEqual([r.situacao for r in primeira], [SIMULADO, SIMULADO, MANTIDO])
        self.assertTrue((self.raiz / "a" / "node_modules").exists())


class TestPastasEComandos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_esvazia_pasta_mas_mantem_a_pasta(self):
        cache = self.base / "cache"
        escrever(cache / "velho" / "a.bin", "0" * 50_000, 30)
        escrever(cache / "novo.bin", "0" * 50_000, 1)
        r = limpar_pastas((Pasta("Cache", cache, idade_minima_dias=7),), False, AGORA)[0]
        self.assertEqual(r.situacao, LIMPO)
        self.assertTrue(cache.is_dir())
        self.assertFalse((cache / "velho").exists())
        self.assertTrue((cache / "novo.bin").exists())
        self.assertIn("1 recentes mantidos", r.detalhe)

    def test_pasta_inexistente_fica_como_mantida(self):
        r = limpar_pastas((Pasta("Nada", self.base / "nao-existe"),), False, AGORA)[0]
        self.assertEqual((r.situacao, r.detalhe), (MANTIDO, "pasta não existe"))

    def test_comando_mede_o_que_liberou(self):
        alvo = self.base / "cache-do-comando"
        escrever(alvo / "lixo.bin", "0" * 200_000)
        apagar = [sys.executable, "-c", f"import shutil; shutil.rmtree({str(alvo)!r})"]
        r = executar_comandos((Comando("Limpador", tuple(apagar), medir=(alvo,)),), False, dict(os.environ))[0]
        self.assertEqual(r.situacao, LIMPO)
        self.assertGreater(r.bytes, 150_000)

    def test_comando_ausente_ou_condicao_falsa_e_pulado(self):
        ambiente = dict(os.environ)
        r1 = executar_comandos((Comando("X", ("comando-que-nao-existe-123",)),), False, ambiente)[0]
        r2 = executar_comandos((Comando("Y", (sys.executable, "-c", "pass"),
                                        condicao=(sys.executable, "-c", "raise SystemExit(1)")),), False, ambiente)[0]
        self.assertEqual((r1.situacao, r2.situacao), (MANTIDO, MANTIDO))
        self.assertIn("não está instalado", r1.detalhe)
        self.assertIn("condição não atendida", r2.detalhe)

    def test_comando_com_erro(self):
        r = executar_comandos((Comando("Falha", (sys.executable, "-c", "raise SystemExit(3)")),), False, dict(os.environ))[0]
        self.assertEqual(r.situacao, ERRO)
        self.assertIn("código 3", r.detalhe)

    def test_simulacao_nao_executa_comando(self):
        marca = self.base / "executou"
        cmd = (sys.executable, "-c", f"open({str(marca)!r}, 'w').close()")
        r = executar_comandos((Comando("Marca", cmd),), True, dict(os.environ))[0]
        self.assertEqual(r.situacao, SIMULADO)
        self.assertFalse(marca.exists())


class TestGit(unittest.TestCase):
    def test_repack_empacota_sem_perder_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True, text=True)
            git("init", "-q")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            for i in range(5):
                (repo / f"a{i}.txt").write_text(str(i) * 1000)
                git("add", ".")
                git("commit", "-qm", f"c{i}")
            cabeca = git("rev-parse", "HEAD").stdout
            self.assertGreater(objetos_soltos(repo)[0], 10)
            mantido = compactar_git((RepoGit("teste", repo, min_objetos_soltos=10_000),), False)[0]
            self.assertEqual(mantido.situacao, MANTIDO)
            r = compactar_git((RepoGit("teste", repo, min_objetos_soltos=1),), False)[0]
            self.assertEqual(r.situacao, LIMPO)
            self.assertEqual(objetos_soltos(repo)[0], 0)
            self.assertEqual(git("rev-parse", "HEAD").stdout, cabeca)
            self.assertEqual(git("rev-list", "--count", "HEAD").stdout.strip(), "5")


class TestSeguranca(unittest.TestCase):
    def test_recusa_link_e_fora_da_raiz(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(os.path.realpath(tmp))
            (base / "raiz").mkdir()
            (base / "outra").mkdir()
            (base / "raiz" / "link").symlink_to(base / "outra")
            with self.assertRaises(RemocaoRecusada):
                verificar_remocao(base / "raiz" / "link", [base / "raiz"])
            with self.assertRaises(RemocaoRecusada):
                verificar_remocao(base / "outra", [base / "raiz"])
            with self.assertRaises(RemocaoRecusada):
                verificar_remocao(base / "raiz", [base / "raiz"])
            verificar_remocao(base / "raiz" / "x", [base / "raiz"])

    def test_config_recusa_pastas_perigosas_e_chaves_erradas(self):
        with tempfile.TemporaryDirectory() as casa, mock.patch.dict(os.environ, {"HOME": casa}):
            origem = Path(casa) / "c.toml"
            for caminho in ("~", "~/Documents", "/tmp/cache"):
                with self.assertRaises(ConfigInvalida):
                    config.interpretar({"pastas": [{"nome": "x", "caminho": caminho}]}, origem)
            with self.assertRaises(ConfigInvalida):
                config.interpretar({"intervalo_dia": 15}, origem)
            with self.assertRaises(ConfigInvalida):
                config.interpretar({"projetos": {"raizes": ["/"]}}, origem)
            ok = config.interpretar({"pastas": [{"nome": "x", "caminho": "~/Library/Caches/x"}]}, origem)
            self.assertEqual(ok.pastas[0].caminho, Path(casa) / "Library" / "Caches" / "x")

    def test_exemplo_do_repositorio_e_valido(self):
        exemplo = Path(__file__).resolve().parent.parent / "config.exemplo.toml"
        cfg = config.carregar(exemplo)
        self.assertEqual(cfg.intervalo_dias, 15)


class TestRelatorio(unittest.TestCase):
    def registro(self):
        return {
            "versao": "0.1.0", "modo": "execucao", "inicio": "2026-10-01T19:52:03-03:00",
            "segundos": 192.4, "proxima": "2026-10-16T19:52:03-03:00",
            "disco_total": 245_000_000_000, "livre_antes": 23_000_000_000, "livre_depois": 27_900_000_000,
            "regras": {"intervalo_dias": 15, "dias_sem_uso": 14, "raizes": ["~/Work"],
                       "regeneraveis": ["node_modules"], "config": "~/.config/faxina/config.toml"},
            "resultados": [
                {"tarefa": "projetos", "nome": "Dependências Node", "alvo": "/x/Work/app/node_modules",
                 "situacao": LIMPO, "bytes": 762_000_000, "estimado": False, "detalhe": "sem alterações há 77 dias", "segundos": 1.2},
                {"tarefa": "comandos", "nome": "<script>", "alvo": "brew cleanup", "situacao": ERRO,
                 "bytes": 0, "estimado": False, "detalhe": "terminou com código 1: a & b", "segundos": 3.0},
                {"tarefa": "projetos", "nome": "Dependências Node", "alvo": "/x/Work/ativo/node_modules",
                 "situacao": MANTIDO, "bytes": 0, "estimado": False, "detalhe": "projeto alterado hoje", "segundos": 0.1},
            ],
            "historico": [{"inicio": "2026-09-16T11:00:00-03:00", "bytes": 1_000_000_000},
                          {"inicio": "2026-10-01T19:52:03-03:00", "bytes": 762_000_000, "atual": True}],
        }

    def test_mesmo_registro_gera_o_mesmo_html(self):
        reg = self.registro()
        a = relatorio.renderizar(reg)
        b = relatorio.renderizar(json.loads(json.dumps(reg)))
        self.assertEqual(a, b)
        self.assertNotIn("{{", a)

    def test_conteudo_e_escapado(self):
        pagina = relatorio.renderizar(self.registro())
        self.assertIn("&lt;script&gt;", pagina)
        self.assertIn("a &amp; b", pagina)
        self.assertIn("Faxina de 1 de outubro de 2026", pagina)
        self.assertIn("Precisa de atenção", pagina)
        self.assertIn("Mantido de propósito (1)", pagina)
        self.assertIn(formatar_bytes(762_000_000), pagina)

    def test_modelo_e_dados_precisam_bater(self):
        with self.assertRaises(ValueError):
            relatorio.preencher("<p>{{a}}</p>", {"a": "1", "b": "2"})
        with self.assertRaises(ValueError):
            relatorio.preencher("<p>{{a}}{{c}}</p>", {"a": "1"})


class TestPontaAPonta(unittest.TestCase):
    def test_simulacao_completa_gera_relatorio(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(os.path.realpath(tmp))
            projeto(base / "work", "velho", idade=1500)  # meses antes do relógio real
            cfg = config.Config(
                origem=base / "c.toml",
                relatorios_dir=base / "relatorios",
                estado_dir=base / "estado",
                projetos=Projetos(ativo=True, raizes=(base / "work",), regeneraveis=(NODE,)),
            )
            reg = faxinar(cfg, simular=True)
            self.assertEqual(reg["modo"], "simulacao")
            self.assertEqual([r["situacao"] for r in reg["resultados"]], [SIMULADO])
            reg["historico"] = []
            pagina, dados = relatorio.salvar(reg, cfg.relatorios_dir)
            self.assertTrue(pagina.exists() and dados.exists())
            self.assertEqual(relatorio.renderizar(json.loads(dados.read_text())), pagina.read_text())
            self.assertTrue((base / "work" / "velho" / "node_modules").exists())


if __name__ == "__main__":
    unittest.main()

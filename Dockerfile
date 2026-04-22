# 多阶段构建:
# - builder 阶段只用来装依赖与包,保留完整工具链。
# - runtime 阶段只拷 venv,去掉编译器 / 缓存,把运行镜像压到最小。
#
# 决策说明:
# - Python 版本锁 3.10(pyproject requires-python 最低档)。匹配 .python-version
#   与 CI 矩阵起点,后续若上 3.11/3.12 只改 FROM,不用动应用代码。
# - 用 pip + venv,不用 uv。uv 在本地与 CI 是首选,但镜像里多一层 uv 依赖收益低,
#   且 CLAUDE.md 明确列了 pip 回退路径 —— 保持镜像构建走那条路径便于排错。
# - ENTRYPOINT 固定 auto-marketing-agent CLI,CMD 默认 --help 不需要 OPENAI_API_KEY
#   也能通过 smoke test,真实使用通过 docker compose run 覆盖 CMD 传入 run 子命令。

ARG PYTHON_VERSION=3.10

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# hatchling 构建会读 pyproject.toml / README.md / LICENSE,缺一不可。
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install .


FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    AMA_TRACING_DISABLED=""

# 非 root 用户:runtime 不需要写盘权限,Sandbox Agent(P2-040)也遵循同规范,
# 镜像层先把用户做好,后续加 scoped credentials 注入不用改 Dockerfile。
RUN groupadd --system --gid 1001 app && \
    useradd --system --uid 1001 --gid app --home-dir /home/app --create-home app

COPY --from=builder /opt/venv /opt/venv

USER app
WORKDIR /home/app

ENTRYPOINT ["auto-marketing-agent"]
CMD ["--help"]

from pathlib import Path

from paperharness.contracts.run import ExecResult


class FakeSandboxSession:
    """测试替身：接口形状必须与 DockerSandboxSession 保持一致。

    刻意**不提供**公开的 `network_enabled` 属性。真沙箱只有私有
    `_network_enabled`（docker_sandbox.py:52），契约 §1.2 也写明沙箱不暴露
    状态查询接口。替身若有公开属性，ACI 里一句
    `getattr(session, "network_enabled", True)` 就能在单测里读到真实值、
    在真沙箱上永远读到默认值 True——造成「单测全绿、隔离失效」的假绿。
    这里改用只读 property 暴露**观察窗口**，名字与真实现一致（前缀下划线），
    调用方不会误以为那是协议的一部分。
    """

    def __init__(
        self,
        results: list[ExecResult] | None = None,
        artifact_path: str | None = None,
        workspace: Path | None = None,
    ):
        self.results = list(results or [])
        self.artifact_path = artifact_path
        self.workspace = workspace or Path(".")
        # 与 DockerSandboxSession 对齐的私有字段名，避免 fake 特有的公开接口
        self._network_enabled = True
        self._closed = False
        self.cleanup_errors: list[str] = []  # 与 DockerSandboxSession 对齐
        self.environment: dict[str, str] = {}
        self.commands: list[list[str]] = []

    # 测试断言用的观察窗口：名字带前缀，不当成 SandboxSession 协议的一部分。
    @property
    def network_state(self) -> bool:
        """仅供测试断言；真沙箱没有这个属性，业务代码不该依赖它。"""
        return self._network_enabled

    @property
    def closed(self) -> bool:
        return self._closed

    def execute(self, command: list[str], timeout_seconds: int) -> ExecResult:
        self.commands.append(command)
        if self.results:
            result = self.results.pop(0)
        else:
            result = ExecResult(
                exit_code=0,
                timed_out=False,
                duration_ms=1,
                stdout_path="logs/fake.log",
                stderr_path="logs/fake.err",
            )
        if result.exit_code == 0 and self.artifact_path:
            artifact = self.workspace / self.artifact_path
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("fake artifact", encoding="utf-8")
        return result

    def set_network(self, enabled: bool) -> None:
        self._network_enabled = enabled

    def set_environment(self, name: str, value: str) -> None:
        self.environment[name] = value

    def export_diff(self) -> str:
        return ""

    def close(self) -> None:
        self._closed = True

import { Component, type ReactNode } from "react";

/** Preserve saved sessions when an unexpected rendering error occurs. */
export class ErrorBoundary extends Component<{children:ReactNode},{failed:boolean}> {
  state={failed:false};
  static getDerivedStateFromError() { return {failed:true}; }
  render() {
    if(!this.state.failed)return this.props.children;
    return <main className="stage"><section className="recovery-screen" role="alert">
      <img src="/assets/brand/cloud-mascot-parsing.png" alt=""/>
      <h1>页面暂时遇到了一点问题</h1>
      <p>服务器上已保存的学习记录不会被清除。请重新打开页面，继续上次的学习。</p>
      <button className="primary" onClick={()=>window.location.reload()}>重新打开</button>
    </section></main>;
  }
}

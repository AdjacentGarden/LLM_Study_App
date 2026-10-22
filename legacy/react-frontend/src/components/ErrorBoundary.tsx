import { Component, type ReactNode } from "react";

/** Preserve saved sessions when an unexpected rendering error occurs. */
export class ErrorBoundary extends Component<{children:ReactNode},{failed:boolean}> {
  state={failed:false};
  static getDerivedStateFromError() { return {failed:true}; }
  render() {
    if(!this.state.failed)return this.props.children;
    return <main className="stage"><section className="recovery-screen" role="alert">
      <h1>页面暂时无法显示</h1>
      <p>请重新打开页面。</p>
      <button className="primary" onClick={()=>window.location.reload()}>重新打开</button>
    </section></main>;
  }
}

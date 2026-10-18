/// <reference types="vite/client" />

// AudioWorklet 脚本以 `?url` 方式引入，Vite 会返回打包后的资源地址字符串
declare module '*?url' {
  const src: string
  export default src
}

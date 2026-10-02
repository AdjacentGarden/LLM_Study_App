import { request as baseRequest } from "../../api/transport";
import { runtimeConfig } from "../config/runtime";
/** Fence late account responses and reject writes made with stale cookies. */
export async function request<T>(path:string,init:RequestInit={},timeoutMs?:number):Promise<T>{
 const owner=new Headers(init.headers).get("X-Demo-Account-Id")??runtimeConfig.defaultUserId;
 if(owner!==runtimeConfig.defaultUserId)throw new Error("账号已切换，旧操作已取消");
 const result=await baseRequest<T>(path,{...init,headers:{...init.headers,"X-Demo-Account-Id":owner}},timeoutMs);
 if(owner!==runtimeConfig.defaultUserId)throw new Error("账号已切换，旧请求结果已失效");return result;
}

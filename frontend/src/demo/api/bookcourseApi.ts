import { runtimeConfig } from "../config/runtime";
import type { Flashcard } from "../types/api";
import type { DemoRepository } from "../services/DemoRepository";
import { request } from "../../api/transport";
/** Public structural contract: no fixture state or private members. */
export type DemoVideoJob={id:string;status:string;asset_url?:string|null;error?:string;result?:{title?:string;explanation?:string}};
export type BookCourseRepository = Pick<DemoRepository, Exclude<keyof DemoRepository,"reset">> & {reviewFlashcard(bookId:string,cardId:string,payload:{rating:"again"|"good";event_id:string}):Promise<Flashcard>;generateLessonVideo(bookId:string,lessonId:string,payload:{reservation_id:string;goal:string;chapter_id:string}):Promise<DemoVideoJob>;getVideoJob(id:string):Promise<DemoVideoJob>};
export const bookcourseApi: BookCourseRepository = new Proxy({} as BookCourseRepository, {
  get(_target, method: string) {
    return async (...args: unknown[]) => {
      if (method === "uploadFile") {
        const data = new FormData(); data.append("file", args[1] as File);
        return request(`/api/demo/bookcourse/uploadFile/${encodeURIComponent(String(args[0]))}`, {method:"POST", headers:{"X-Demo-Account-Id":runtimeConfig.defaultUserId}, body:data});
      }
      const owner=runtimeConfig.defaultUserId;
      const result=await request<unknown>(`/api/demo/bookcourse/${method}`, {method:"POST", headers:{"X-Demo-Account-Id":owner}, body:JSON.stringify({args})});
      if((method==="buildFlashcards" || method==="buildQuizzes") && !Array.isArray(result) && result && typeof result==="object" && "job_id" in result){
        let job=result as {job_id:string;status:string;items?:unknown[];error?:string};
        while(job.status!=="done" && job.status!=="failed") {await new Promise(resolve=>setTimeout(resolve,1500));if(owner!==runtimeConfig.defaultUserId)throw new Error("账号已切换");job=await request(`/api/demo/bookcourse/getJob`,{method:"POST",headers:{"X-Demo-Account-Id":owner},body:JSON.stringify({args:[job.job_id]})});}
        if(job.status==="failed")throw new Error(job.error??"练习生成失败");return job.items??[];
      }
      if(owner!==runtimeConfig.defaultUserId)throw new Error("账号已切换，旧请求已失效");return result;
    };
  }
});

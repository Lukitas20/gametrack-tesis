import { api } from "../api.js";
import { state, isLoggedIn, isDeveloper } from "../store.js";
import { h, icon, mount, openModal, modalHead, toast } from "../ui.js";

export const enjoymentLabel={liked:"Me gustó",disliked:"No me gustó",mixed:"Tengo sensaciones mixtas",not_sure:"Todavía no sé"};
export const reasonLabel={experience:"La experiencia de juego",technical:"Problemas técnicos",social:"La partida con amigos se interrumpió",time:"Me faltó tiempo",other:"Otra razón"};
export let feedbackRevision=0;

export function openPlayFeedback(gameId,name="este juego",onSaved=()=>{}) {
  if(!isLoggedIn() || isDeveloper()) return;
  const owner=state.user.id;
  openModal(close=>{
    const root=h("div",{class:"play-feedback-body"},h("p",{role:"status"},"Leyendo tu última devolución…"));
    const current=()=>owner===state.user?.id && root.isConnected;
    async function load() {
      try {const data=await api.playFeedback(gameId);if(current()) render(data || {});}
      catch(error) {if(current()) mount(root,h("p",{role:"alert"},error.message),h("button",{class:"btn",onClick:load},"Reintentar"));}
    }
    function render(data) {
      const played=h("input",{type:"checkbox",checked:data.played ?? true});
      const enjoy=h("select",{id:"feedback-enjoy"},Object.entries(enjoymentLabel).map(([value,label])=>h("option",{value},label)));enjoy.value=data.enjoyment || "not_sure";
      const reason=h("select",{id:"feedback-reason"},Object.entries(reasonLabel).map(([value,label])=>h("option",{value},label)));reason.value=data.reason || "experience";
      const replay=h("select",{id:"feedback-replay"},h("option",{value:""},"Todavía no sé"),h("option",{value:"yes"},"Sí"),h("option",{value:"no"},"No"));replay.value=data.replay==null ? "" : data.replay ? "yes" : "no";
      const minutes=h("input",{id:"feedback-minutes",type:"number",min:"0",max:"10080",step:"1",value:data.minutes ?? "",placeholder:"Opcional"});
      const note=h("textarea",{id:"feedback-note",rows:"3",maxlength:"500",placeholder:"¿Qué pasó? Podés dejar una nota para vos."},data.note || "");
      const notice=h("p",{class:"play-learning-note"}),errorBox=h("p",{role:"alert"}),submit=h("button",{type:"submit",class:"btn btn-primary"},"Guardar devolución",icon("check",15));
      function explain() {
        enjoy.disabled=!played.checked;minutes.disabled=!played.checked;
        if(!played.checked) {enjoy.value="not_sure";minutes.value="";}
        notice.textContent=played.checked && reason.value==="experience" && ["liked","disliked"].includes(enjoy.value) ?
          "Tu opinión ayudará a ordenar próximas recomendaciones. Tus estrellas no se modifican." :
          "Esto se guarda como contexto. No confundimos una interrupción o una opinión incierta con que te disguste el juego.";
      }
      for(const control of [played,enjoy,reason]) control.addEventListener("change",explain);explain();
      const field=(label,node)=>h("div",{class:"play-feedback-field"},h("label",{for:node.id},label),node);
      mount(root,h("p",null,`Contanos cómo te fue con ${name}. Podés editar esta devolución más adelante.`),h("form",{class:"play-feedback-form",onSubmit:async event=>{
        event.preventDefault();if(!current() || submit.disabled) return;
        submit.disabled=true;submit.textContent="Guardando…";errorBox.textContent="";
        try {
          const result=await api.savePlayFeedback(gameId,{played:played.checked,enjoyment:enjoy.value,reason:reason.value,replay:replay.value==="" ? null : replay.value==="yes",minutes:minutes.value==="" ? null : Number(minutes.value),note:note.value.trim() || null});
          if(!current()) return;feedbackRevision+=1;close();toast(result.message);onSaved(result);
        } catch(error) {if(current()){errorBox.textContent=error.message;submit.disabled=false;submit.textContent="Guardar devolución";}}
      }},h("label",{class:"play-feedback-check"},played,"Llegué a jugar"),
        h("div",{class:"play-feedback-grid"},field("¿Te gustó?",enjoy),field("¿Qué explica tu experiencia?",reason),field("¿Lo volverías a jugar?",replay),field("Minutos de esta sesión (declarados por vos)",minutes)),
        field("Nota personal (opcional)",note),notice,errorBox,h("div",{class:"play-feedback-footer"},h("a",{href:"#/que-jugamos?modo=experiencias",onClick:close},"Mis opiniones"),submit)));
    }
    load();return h("div",null,modalHead("¿Cómo estuvo la partida?","Una devolución breve para mejorar tus próximas sugerencias.",close),root);
  });
}

export function playFeedbackButton(gameId,name,onSaved) {
  return isLoggedIn() && !isDeveloper() ? h("button",{class:"btn btn-ghost btn-sm",type:"button",onClick:()=>openPlayFeedback(gameId,name,onSaved)},icon("heart",14),"Ya lo probé: contar cómo me fue") : null;
}

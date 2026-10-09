(function(){
  var primary='xiaomuwu-shelf',backup='xiaomuwu-shelf-backup';
  function valid(value){try{return Array.isArray(JSON.parse(value||''))}catch(error){return false}}
  try{var current=localStorage.getItem(primary),saved=localStorage.getItem(backup);if(!valid(current)&&valid(saved))localStorage.setItem(primary,saved);else if(valid(current)&&current!==saved)localStorage.setItem(backup,current)}catch(error){}
  if(window.__xiaomuwuShelfBackup)return;window.__xiaomuwuShelfBackup=true;
  var original=Storage.prototype.setItem;
  Storage.prototype.setItem=function(key,value){original.call(this,key,value);if(key===primary)original.call(this,backup,value)};
})();

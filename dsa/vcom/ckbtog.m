  function state=ckbtog(h_cb,s1,s2)
% function state=ckbtog(h_cb,s1,s2);
% Toggle state of a "check button" , not to be confused
% with a check box 
% Dick Benson, DSP Technology
  if strcmp(s1, get(h_cb,'String')),
     set(h_cb,'String',s2,'UserData',1);
     state = 1;
  else  
     set(h_cb,'String',s1,'UserData',0);
     state = 0; 
  end;
  

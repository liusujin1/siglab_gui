function macros(vi,label);   % adds a macros menu to a vi
  [drv,pth] = pathfind(vi);
  f = fopen([drv pth '\' vi 'mac.m']);
  if f ~= -1
    s = uimenu('Label',label);  m=0;  w=' ';
    while ischar(w)
      w = fgetl(f);  p = findstr(w,'case ''');
      if length(p)
        w = w(p(1)+6:end);  p = findstr(w,'''');  w = w(1:p(1)-1);  m = m+1;
        uimenu(s,'Label',w,'Accel',char('0'+m),'CallBack',...
          ['assignin(''base'',''' upper(vi) 'mac'',''' w ''');' vi 'mac;']);
      end;
    end;
    fclose(f);
  end;
